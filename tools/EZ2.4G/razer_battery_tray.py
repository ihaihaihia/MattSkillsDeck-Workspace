# -*- coding: utf-8 -*-
"""雷蛇 Nari Ultimate（1532:051A）无线耳机电量托盘小程序（单文件）。

纯 Python 3 标准库实现：ctypes 直接调 SetupAPI / hid.dll 读电量，
用 Shell_NotifyIconW 把一个「程序内绘制」的绿色圆点放进任务栏通知区；
左键点击绿点 → 在屏幕右下第 4 列 / 第 5 行位置弹出电量卡片（GDI 绘制、不抢焦点），
再次点击绿点或点击面板外部 → 收起；右键菜单保留「立即刷新 / 退出」。

用法：
    python razer_battery_tray.py           # 托盘常驻模式（默认）
    python razer_battery_tray.py --once    # 只读一次，打印结果后退出（验收用）
    python razer_battery_tray.py --demo    # 启动后直接弹出面板（截图验收用）
    python razer_battery_tray.py --quit    # 让正在运行的实例正常退出

HID 协议（来源 srsp2282/razer-nari-linux docs/protocol.md，已实测）：
    请求：feature report id = 0xFF，
          内容 ff 0a 00 fd 04 12 f1 02 05，其余补 0 到 64 字节
    响应（64 字节 feature report，含 report id 在 byte0，故偏移与规格一致）：
        byte1-2   = 链路状态，小端 0x0F05 在线 / 0x0100 离线（离线读到的是接收器缓存）
        byte9     = 充电状态，0x03 放电中 / 0x05 充电中 / 0x06 已充满
        byte12-13 = 电压 mV，大端
        byte14    = 固件百分比（粗粒度）

注意：本程序只在内存里工作，不读写任何配置目录，也不改注册表和设备设置。
图标与卡片全部由 GDI 现场绘制，不依赖任何外部图片资源。
"""

import ctypes
import json
import os
import sys
import tempfile
import threading
import time
from ctypes import wintypes as wt

# ---------------------------------------------------------------------------
# 一、HID 协议层（SetupAPI 枚举 + HidD_* feature report）
# ---------------------------------------------------------------------------

VID = 0x1532                 # 雷蛇
PID = 0x051A                 # Nari Ultimate 无线接收器
REPORT_ID = 0xFF             # 本接口唯一的 feature ReportID（传 0x09/0x00 会 err=87）
REPORT_LEN = 64
# 请求头：ff 0a 00 fd 04 12 f1 02 05，其余补 0
REQUEST = bytes([0xFF, 0x0A, 0x00, 0xFD, 0x04, 0x12, 0xF1, 0x02, 0x05])
LINK_ONLINE = 0x0F05         # byte1-2 小端拼出来的在线标志

STATUS_TEXT = {
    0x00: "未知",
    0x01: "未知",
    0x03: "放电中",
    0x05: "充电中",
    0x06: "已充满",
}

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_RW = 0x00000001 | 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = wt.HANDLE(-1).value


def is_invalid_handle(h):
    """判断 Windows 句柄是否无效。

    ctypes 的 HANDLE 字段是 c_void_p，与整数是身份比较，`c_void_p(-1) == 0xFFFF...`
    恒为 False，而 `bool(c_void_p(-1))` 又为 True —— 所以既不能写 `== INVALID_HANDLE_VALUE`
    也不能只写 `not h`。这里统一取 .value 后比较。
    """
    if not h:
        return True
    return getattr(h, "value", h) == INVALID_HANDLE_VALUE


DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10


class GUID(ctypes.Structure):
    """最小的 GUID 结构，只需能表达设备接口类 GUID。"""

    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, s=""):
        super().__init__()
        s = (s or "{00000000-0000-0000-0000-000000000000}").strip().strip("{}")
        a, b, c, rest = s.split("-", 3)
        self.Data1 = int(a, 16)
        self.Data2 = int(b, 16)
        self.Data3 = int(c, 16)
        body = bytes.fromhex(rest.replace("-", ""))
        for i in range(8):
            self.Data4[i] = body[i]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wt.ULONG), ("VendorID", wt.USHORT),
                ("ProductID", wt.USHORT), ("VersionNumber", wt.USHORT)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wt.USHORT), ("UsagePage", wt.USHORT),
        ("InputReportByteLength", wt.USHORT),
        ("OutputReportByteLength", wt.USHORT),
        ("FeatureReportByteLength", wt.USHORT),
        ("Reserved", wt.USHORT * 17),
        ("NumberLinkCollectionNodes", wt.USHORT),
        ("NumberInputButtonCaps", wt.USHORT),
        ("NumberInputValueCaps", wt.USHORT),
        ("NumberInputDataIndices", wt.USHORT),
        ("NumberOutputButtonCaps", wt.USHORT),
        ("NumberOutputValueCaps", wt.USHORT),
        ("NumberOutputDataIndices", wt.USHORT),
        ("NumberFeatureButtonCaps", wt.USHORT),
        ("NumberFeatureValueCaps", wt.USHORT),
        ("NumberFeatureDataIndices", wt.USHORT),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wt.DWORD), ("Reserved", ctypes.c_void_p)]


hid = ctypes.WinDLL("hid", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

kernel32.CreateFileW.restype = wt.HANDLE
kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                 wt.DWORD, wt.DWORD, wt.HANDLE]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]

setupapi.SetupDiGetClassDevsW.restype = wt.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wt.LPCWSTR,
                                          wt.HWND, wt.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID), wt.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wt.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
    wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wt.HANDLE]

hid.HidD_GetAttributes.argtypes = [wt.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidD_SetFeature.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidD_GetFeature.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]

GUID_DEVINTERFACE_HID = GUID("{4D1E55B2-F16F-11CF-88CB-001111000030}")
# SP_DEVICE_INTERFACE_DETAIL_DATA_W 的 cbSize：x64 为 8，x86 为 6
DETAIL_CBSIZE = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6


class HidError(Exception):
    """设备不存在、打开失败或收发失败。"""


def _open_path(path, access):
    return kernel32.CreateFileW(path, access, FILE_SHARE_RW, None,
                                OPEN_EXISTING, 0, None)


def enumerate_hid():
    """枚举当前所有 HID 接口，返回 [(path, vid, pid, usage_page, feature_len)]。"""
    out = []
    hdev = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(GUID_DEVINTERFACE_HID), None, None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if is_invalid_handle(hdev):
        return out
    try:
        idx = 0
        while True:
            did = SP_DEVICE_INTERFACE_DATA()
            did.cbSize = ctypes.sizeof(did)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                    hdev, None, ctypes.byref(GUID_DEVINTERFACE_HID), idx,
                    ctypes.byref(did)):
                break
            idx += 1
            need = wt.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(did), None, 0, ctypes.byref(need), None)
            if not need.value:
                continue
            buf = ctypes.create_string_buffer(need.value)
            ctypes.cast(buf, ctypes.POINTER(wt.DWORD))[0] = DETAIL_CBSIZE
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                    hdev, ctypes.byref(did), buf, need.value,
                    ctypes.byref(need), None):
                continue
            # 设备路径紧跟在 cbSize(DWORD) 之后，偏移 4
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            # 只为了拿 VID/PID/UsagePage/FeatureReportByteLength 才打开句柄
            h = _open_path(path, 0)
            if is_invalid_handle(h):
                continue
            try:
                attrs = HIDD_ATTRIBUTES()
                attrs.Size = ctypes.sizeof(attrs)
                if not hid.HidD_GetAttributes(h, ctypes.byref(attrs)):
                    continue
                caps = HIDP_CAPS()
                pp = ctypes.c_void_p()
                if hid.HidD_GetPreparsedData(h, ctypes.byref(pp)):
                    hid.HidP_GetCaps(pp, ctypes.byref(caps))
                    hid.HidD_FreePreparsedData(pp)
                out.append((path, attrs.VendorID, attrs.ProductID,
                            caps.UsagePage, caps.FeatureReportByteLength))
            finally:
                kernel32.CloseHandle(h)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return out


def find_interface():
    """定位 usage page 0xFF00 且 feature 报告长度 >= 64 的那个接口。"""
    for path, vid, pid, upage, flen in enumerate_hid():
        if vid == VID and pid == PID and upage == 0xFF00 and flen >= REPORT_LEN:
            return path
    return None


class Reading(object):
    """一次读数的结果。"""

    def __init__(self, online, voltage, percent, status, raw):
        self.online = online          # bool：耳机是否与接收器在线
        self.voltage = voltage        # int：mV
        self.percent = percent        # int：%
        self.status = status          # int：充电状态原始字节
        self.raw = raw                # bytes：64 字节原始响应（排错用）

    @property
    def status_text(self):
        return STATUS_TEXT.get(self.status, "未知")


def _send_once(path):
    """一次完整的 set/get feature 往返，返回 64 字节原始响应。"""
    h = _open_path(path, GENERIC_READ | GENERIC_WRITE)
    if is_invalid_handle(h):
        h = _open_path(path, 0)          # 部分机型不给写权限，退回只读
    if is_invalid_handle(h):
        raise HidError("打开 HID 接口失败（设备可能已被拔出）")
    try:
        req = (REQUEST + bytes(REPORT_LEN))[:REPORT_LEN]
        sbuf = ctypes.create_string_buffer(req, REPORT_LEN)
        if not hid.HidD_SetFeature(h, sbuf, REPORT_LEN):
            raise HidError("HidD_SetFeature 失败（错误码 %d）" % ctypes.get_last_error())
        time.sleep(0.08)                 # 给设备一点时间准备响应
        gbuf = ctypes.create_string_buffer(REPORT_LEN)
        ctypes.cast(gbuf, ctypes.POINTER(ctypes.c_ubyte))[0] = REPORT_ID
        if not hid.HidD_GetFeature(h, gbuf, REPORT_LEN):
            raise HidError("HidD_GetFeature 失败（错误码 %d）" % ctypes.get_last_error())
        return bytes(gbuf.raw[:REPORT_LEN])
    finally:
        kernel32.CloseHandle(h)


def parse(raw):
    """解析 64 字节特征报告（byte0 是 report id，故偏移与规格文档一致）。"""
    link = (raw[1] << 8) | raw[2]
    status = raw[9]
    voltage = (raw[12] << 8) | raw[13]     # 大端
    percent = raw[14]
    return Reading(link == LINK_ONLINE, voltage, percent, status, raw)


# 实时电量百分比的锚点文件（%LOCALAPPDATA%\EZ2.4G\battery_anchors.json，跨会话累积校准）
ANCHOR_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                          "EZ2.4G")
ANCHOR_PATH = os.path.join(ANCHOR_DIR, "battery_anchors.json")
ANCHOR_MV_PER_10PCT = 50.0        # 单锚点退化时的默认斜率：每 10% 约 50mV
ANCHOR_EMA_OLD = 0.7              # 同档位电压的 EMA 平滑权重（旧值）
ANCHOR_EMA_NEW = 0.3              # 同档位电压的 EMA 平滑权重（新采样）


class BatteryPercentTracker(object):
    """实时电量百分比：用电压 + 设备粗档位做分段线性插值。

    设备上报的 raw[14] 是 10% 一档的粗档位，raw[12..13] 的电压是实时的。
    以「各粗档位对应的平滑电压」为锚点，当前电压落在相邻两档之间时线性插值，
    界面就能 1% 一跳地显示，不必等设备跳档。

    约束（避免与设备自相矛盾）：
      * 结果 clamp 在 [coarse - 9, coarse]，绝不高于设备档位；
      * 锚点不足 2 个时按默认 50mV / 10% 估算，并做同样的 clamp。
    """

    def __init__(self, path=None):
        self.path = path if path is not None else ANCHOR_PATH
        self.anchors = {}                 # {粗档位: 该档位的平滑电压 mV}
        self._load()

    # ---- 锚点持久化（读写失败一律静默）----
    def _load(self):
        """从 JSON 读回锚点表；文件不存在或损坏都当空表处理。"""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for key, val in data.items():
                    try:
                        c, v = int(key), float(val)
                    except (TypeError, ValueError):
                        continue
                    if 0 <= c <= 100 and 2000.0 <= v <= 6000.0:
                        self.anchors[c] = v
        except Exception:
            self.anchors = {}

    def _save(self):
        """把锚点表写回 JSON；写失败不影响读数。"""
        try:
            d = os.path.dirname(self.path)
            if d and not os.path.isdir(d):
                os.makedirs(d)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({str(c): round(v, 2) for c, v in self.anchors.items()},
                          f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:
            pass

    # ---- 采样与估算 ----
    def update(self, mv, coarse):
        """喂一次 (电压 mV, 设备粗档位)，更新锚点并返回实时百分比（float）。"""
        try:
            mv = int(mv)
            coarse = int(coarse)
        except (TypeError, ValueError):
            return float(coarse or 0)
        if coarse in self.anchors:
            # 同一档位再采样：EMA 平滑，避免电压抖动直接传导到界面
            self.anchors[coarse] = (self.anchors[coarse] * ANCHOR_EMA_OLD
                                    + mv * ANCHOR_EMA_NEW)
        else:
            self.anchors[coarse] = float(mv)
        self._save()
        return self.estimate(mv, coarse)

    def estimate(self, mv, coarse):
        """按现有锚点估算（不改锚点；自测可直接构造 anchors 后调用）。"""
        try:
            mv = int(mv)
            coarse = int(coarse)
        except (TypeError, ValueError):
            return float(coarse or 0)
        pts = sorted((v, c) for c, v in self.anchors.items())   # (电压, 档位)
        if len(pts) >= 2:
            lower = [p for p in pts if p[0] <= mv]
            upper = [p for p in pts if p[0] > mv]
            if lower and upper:
                # 相邻两档之间：分段线性插值
                v0, c0 = lower[-1]
                v1, c1 = upper[0]
                value = c0 if v1 == v0 else c0 + (c1 - c0) * (mv - v0) / float(v1 - v0)
            elif lower:
                # 比所有锚点都高：从最高锚点按默认斜率外推
                v0, c0 = lower[-1]
                value = c0 + (mv - v0) / ANCHOR_MV_PER_10PCT * 10.0
            else:
                # 比所有锚点都低：从最低锚点向下外推
                v1, c1 = upper[0]
                value = c1 - (v1 - mv) / ANCHOR_MV_PER_10PCT * 10.0
        elif len(pts) == 1:
            # 只有一个锚点：默认每 10% 约 50mV，按当前档位做偏移
            v0, c0 = pts[0]
            base = self.anchors.get(coarse, v0)
            value = coarse - (base - mv) / ANCHOR_MV_PER_10PCT * 10.0
        else:
            value = float(coarse)
        # clamp：不高于设备档位，也不低于下一档（coarse-9）
        lo = max(0, coarse - 9)
        hi = max(lo, coarse)
        return float(max(lo, min(hi, value)))


def read_battery(retries=2):
    """读一次电量，失败会重试；彻底失败抛 HidError。"""
    last = None
    for _ in range(max(1, retries)):
        path = find_interface()
        if not path:
            last = HidError("未找到雷蛇 Nari Ultimate 接收器（设备未插入？）")
        else:
            try:
                return parse(_send_once(path))
            except (HidError, OSError) as exc:
                last = exc
        time.sleep(0.25)
    raise last or HidError("读取失败")


# ---------------------------------------------------------------------------
# 二、Win32 外壳：常量、GDI 结构体与函数签名
# ---------------------------------------------------------------------------

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT,
                             wt.WPARAM, wt.LPARAM)

WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
WM_QUERYENDSESSION = 0x0011
WM_MOUSEACTIVATE = 0x0021
WM_COMMAND = 0x0111
WM_TIMER = 0x0113
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_CAPTURECHANGED = 0x0215
WM_CONTEXTMENU = 0x007B
WM_CANCELMODE = 0x001F
WM_APP = 0x8000
WM_TRAY = WM_APP + 1              # 托盘回调消息

# 版本 4 托盘通知：左键不再发 WM_LBUTTONUP，而发 NIN_SELECT
NIN_SELECT = 0x0400
NIN_KEYSELECT = 0x0401

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIM_SETVERSION = 4

NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NOTIFYICON_VERSION_4 = 4

IDI_APPLICATION = 32512

TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100

CMD_REFRESH = 1001
CMD_QUIT = 1002
TIMER_ID = 1
REFRESH_MS = 30000                # 30 秒刷新一次

# ---- 低电量预警气泡参数（全部在预警窗口自己的 WM_TIMER / WndProc 里处理）----
WARN_TIMER_ID = 1                 # 预警窗口自己的计时器（窗口不同，id 可与托盘复用）
WARN_LOW_PCT = 10                 # 低于该百分比（严格小于）才预警
STATUS_DISCHARGING = 0x03         # 只有「放电中」算真正在用电池
WARN_AUTO_HIDE_MS = 12000         # 弹出后 12 秒自动收起
WARN_REPEAT_S = 300               # 电量持续偏低时，距上次弹出 5 分钟后再次提醒
WARN_DEMO_MS = 4000               # --warn-demo 用 4 秒自动收起
WARN_ALIGN_TOL = 8                # 气泡中心与图标中心的允许误差（px）
WARN_EDGE_MARGIN = 8              # 工作区四周最小留白（px）
WARN_W_RATIO = 0.36               # 气泡宽 = 主面板宽 * 0.36
WARN_H_RATIO = 0.28               # 气泡高 = 主面板高 * 0.28（面积比 0.1008）

CLASS_NAME = "RazerNariBatteryTrayWnd"
PANEL_CLASS_NAME = "RazerNariBatteryPanelWnd"
WARN_CLASS_NAME = "RazerNariBatteryWarnWnd"      # 低电量预警气泡
NARI_TITLE = "雷蛇 Nari Ultimate"
DEVICE_NAME = "Razer Nari Ultimate 雷蛇影鲛终极版"
DEVICE_SUB = "2.4G 无线"

# 窗口样式
WS_POPUP = 0x80000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
SW_HIDE = 0
SW_SHOWNORMAL = 1
MA_NOACTIVATE = 3
HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
PM_REMOVE = 0x0001

# SystemParametersInfo
SPI_GETWORKAREA = 0x0030
SM_CXSCREEN = 0
SM_CYSCREEN = 1

# GDI
TRANSPARENT = 1
NULL_BRUSH = 5
NULL_PEN = 8
PS_SOLID = 0
BI_RGB = 0
DIB_RGB_COLORS = 0
SRCCOPY = 0x00CC0020
CLEARTYPE_QUALITY = 5
DEFAULT_CHARSET = 1
FW_NORMAL = 400
FW_BOLD = 700
FONT_FACE = "微软雅黑"

DT_LEFT = 0x0000
DT_CENTER = 0x0001
DT_RIGHT = 0x0002
DT_VCENTER = 0x0004
DT_SINGLELINE = 0x0020
DT_NOPREFIX = 0x0800
DT_END_ELLIPSIS = 0x8000
DT_CALCRECT = 0x0400

# 面板配色（照华为卡片：浅灰底、深灰字、绿点、浅紫条）
COLOR_CARD = (0xF7, 0xF7, 0xF9)
COLOR_TEXT = (0x1F, 0x1F, 0x1F)
COLOR_SUB = (0x8A, 0x8F, 0x98)
COLOR_GREEN = (0x3E, 0xC6, 0x5A)
COLOR_BAT_FRAME = (0x6B, 0x72, 0x80)
COLOR_BAT_HIGH = (0x4C, 0xAF, 0x50)
COLOR_BAT_MID = (0xFF, 0xA0, 0x00)
COLOR_BAT_LOW = (0xE5, 0x39, 0x35)
COLOR_BAR = (0xEF, 0xEA, 0xFB)
COLOR_BAR_TEXT = (0x5B, 0x21, 0xB6)
COLOR_LINE = (0x4B, 0x55, 0x63)
COLOR_CUP = (0xE5, 0xE7, 0xEB)
COLOR_CUP_INNER = (0xD1, 0xD5, 0xDB)

# 低电量预警气泡配色（深色底 + 低电量红；字体仍走 _make_font 的微软雅黑）
COLOR_WARN_BG = (0x24, 0x2A, 0x33)
COLOR_WARN_NUM = (0xFF, 0x5A, 0x48)
COLOR_WARN_HINT = (0xC9, 0xD0, 0xDA)
COLOR_WARN_BORDER = COLOR_LINE
WARN_HINT_TEXT = "大肥鱼提醒你该充电了"

# 卡片基准尺寸（以 1920 宽屏幕 = k 1.0 为基准，整体按 k 等比放大）
BASE_CELL_W = 480
BASE_CARD_H = 200
BASE_MARGIN = 8


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class NOTIFYICONDATAW(ctypes.Structure):
    """完整的 NOTIFYICONDATAW（Vista+ 版），保证 cbSize 正确。"""

    _fields_ = [
        ("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
        ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128), ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
        ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD), ("guidItem", GUID),
        ("hBalloonIcon", wt.HICON),
    ]


class NOTIFYICONIDENTIFIER(ctypes.Structure):
    """Shell_NotifyIconGetRect 的入参（Vista+），用于自测时定位图标真实位置。"""

    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
                ("guidItem", GUID)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wt.HDC), ("fErase", wt.BOOL), ("rcPaint", wt.RECT),
                ("fRestore", wt.BOOL), ("fIncUpdate", wt.BOOL),
                ("rgbReserved", ctypes.c_ubyte * 32)]


# ---- User32 ----
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wt.HWND, wt.HMENU,
                                   wt.HINSTANCE, ctypes.c_void_p]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.KillTimer.argtypes = [wt.HWND, ctypes.c_size_t]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.LoadIconW.restype = wt.HICON
user32.LoadIconW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
user32.DestroyIcon.argtypes = [wt.HICON]
user32.RegisterWindowMessageW.restype = wt.UINT
user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.SetMenuDefaultItem.argtypes = [wt.HMENU, wt.UINT, wt.UINT]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wt.HWND,
                                  ctypes.c_void_p]
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.restype = ctypes.c_int
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.DrawTextW.restype = ctypes.c_int
user32.DrawTextW.argtypes = [wt.HDC, wt.LPCWSTR, ctypes.c_int,
                             ctypes.POINTER(wt.RECT), wt.UINT]
user32.BeginPaint.restype = wt.HDC
user32.BeginPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.EndPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.InvalidateRect.argtypes = [wt.HWND, ctypes.c_void_p, wt.BOOL]
user32.UpdateWindow.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.IsWindowVisible.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.SetWindowPos.restype = wt.BOOL
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.PeekMessageW.restype = wt.BOOL
user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT,
                                wt.UINT, wt.UINT]
user32.SetWindowRgn.argtypes = [wt.HWND, wt.HRGN, wt.BOOL]
user32.SetCapture.restype = wt.HWND
user32.SetCapture.argtypes = [wt.HWND]
user32.ReleaseCapture.restype = wt.BOOL
user32.ReleaseCapture.argtypes = []
user32.GetCapture.restype = wt.HWND
user32.GetCapture.argtypes = []
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.SystemParametersInfoW.restype = wt.BOOL
user32.SystemParametersInfoW.argtypes = [wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.CreateIconIndirect.restype = wt.HICON
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]

# ---- GDI32 ----
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wt.HANDLE,
                                   wt.DWORD]
gdi32.CreateBitmap.restype = wt.HBITMAP
gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wt.UINT, wt.UINT,
                               ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.CreateSolidBrush.restype = wt.HBRUSH
gdi32.CreateSolidBrush.argtypes = [wt.DWORD]
gdi32.CreatePen.restype = wt.HPEN
gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wt.DWORD]
gdi32.CreateFontW.restype = wt.HFONT
gdi32.CreateFontW.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wt.DWORD, wt.DWORD,
                              wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD,
                              wt.DWORD, wt.LPCWSTR]
gdi32.GetStockObject.restype = wt.HGDIOBJ
gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.SetBkMode.argtypes = [wt.HDC, ctypes.c_int]
gdi32.SetTextColor.restype = wt.DWORD
gdi32.SetTextColor.argtypes = [wt.HDC, wt.DWORD]
user32.FillRect.restype = ctypes.c_int          # FillRect 在 user32.dll，不在 gdi32
user32.FillRect.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH]
gdi32.RoundRect.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                            ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.Ellipse.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int]
gdi32.PolyBezier.argtypes = [wt.HDC, ctypes.POINTER(wt.POINT), wt.DWORD]
gdi32.Polygon.argtypes = [wt.HDC, ctypes.POINTER(wt.POINT), ctypes.c_int]
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int,
                         wt.DWORD]
gdi32.CreateRoundRectRgn.restype = wt.HRGN
gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int]

shell32.Shell_NotifyIconW.argtypes = [wt.DWORD,
                                      ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wt.BOOL
shell32.Shell_NotifyIconGetRect.argtypes = [ctypes.POINTER(NOTIFYICONIDENTIFIER),
                                            ctypes.POINTER(wt.RECT)]
shell32.Shell_NotifyIconGetRect.restype = ctypes.c_long
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.mouse_event.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD,
                               ctypes.c_size_t]


# ---------------------------------------------------------------------------
# 三、GDI 绘图小工具 + 程序内生成的绿点托盘图标
# ---------------------------------------------------------------------------

def _rgb(color):
    """(r, g, b) → COLORREF。"""
    r, g, b = color
    return r | (g << 8) | (b << 16)


def _make_font(px, bold=False):
    """按像素高度创建「微软雅黑」字体；调用方负责 DeleteObject。"""
    return gdi32.CreateFontW(
        -max(1, int(round(px))), 0, 0, 0, FW_BOLD if bold else FW_NORMAL,
        0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, FONT_FACE)


def _draw_text(hdc, text, x, y, w, h, px, bold=False, color=COLOR_TEXT,
               align=DT_LEFT, vcenter=True):
    """GDI 单行文字：透明背景 + 微软雅黑 + 垂直居中。"""
    font = _make_font(px, bold)
    old = gdi32.SelectObject(hdc, font)
    try:
        gdi32.SetTextColor(hdc, _rgb(color))
        gdi32.SetBkMode(hdc, TRANSPARENT)
        rc = wt.RECT(int(x), int(y), int(x + w), int(y + h))
        flags = align | DT_SINGLELINE | DT_NOPREFIX
        if vcenter:
            flags |= DT_VCENTER
        user32.DrawTextW(hdc, text, -1, ctypes.byref(rc), flags)
    finally:
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(font)


def _text_width(hdc, text, px, bold=False):
    """量一段文字的像素宽度（用同一个 GDI 字体，保证与绘制一致）。"""
    font = _make_font(px, bold)
    old = gdi32.SelectObject(hdc, font)
    try:
        rc = wt.RECT(0, 0, 0, 0)
        user32.DrawTextW(hdc, text, -1, ctypes.byref(rc),
                         DT_CALCRECT | DT_SINGLELINE | DT_NOPREFIX)
        return int(rc.right)
    finally:
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(font)


def _fill(hdc, x, y, w, h, color):
    """纯色填充矩形（用于底色）。"""
    brush = gdi32.CreateSolidBrush(_rgb(color))
    rc = wt.RECT(int(x), int(y), int(x + w), int(y + h))
    user32.FillRect(hdc, ctypes.byref(rc), brush)
    gdi32.DeleteObject(brush)


def make_green_dot_icon(size=32):
    """程序内绘制绿色圆点托盘图标（1px 级抗锯齿），返回 HICON（失败返回 0）。

    做法：CreateDIBSection 建 32bpp 位图 → 逐像素按 4x4 超采样填 BGRA
    （圆外 alpha=0，边缘按覆盖率给 alpha）→ 单色 AND mask 全 0 →
    CreateIconIndirect 得到带 alpha 的 HICON。全程无外部资源。
    """
    hdc_screen = user32.GetDC(None)
    if not hdc_screen:
        return 0
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = size
    bmi.bmiHeader.biHeight = -size          # 负高度 = 自上而下，行序与直觉一致
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB
    bits = ctypes.c_void_p()
    hbm_color = gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bmi),
                                       DIB_RGB_COLORS, ctypes.byref(bits),
                                       None, 0)
    # 1bpp 单色位图，每行按 WORD 对齐；全 0 = AND mask 不遮挡彩色位图
    row_bytes = ((size + 15) // 16) * 2
    mask_buf = ctypes.create_string_buffer(row_bytes * size)   # 已全 0
    hbm_mask = gdi32.CreateBitmap(size, size, 1, 1,
                                  ctypes.cast(mask_buf, ctypes.c_void_p))
    try:
        if not hbm_color or not hbm_mask or not bits.value:
            return 0
        px = (ctypes.c_ubyte * (size * size * 4)).from_address(bits.value)
        cx = cy = (size - 1) / 2.0
        radius = size * 0.36
        r, g, b = COLOR_GREEN
        for y in range(size):
            for x in range(size):
                hit = 0
                for sy in range(4):
                    for sx in range(4):
                        dx = x + (sx + 0.5) / 4.0 - cx
                        dy = y + (sy + 0.5) / 4.0 - cy
                        if dx * dx + dy * dy <= radius * radius:
                            hit += 1
                i = (y * size + x) * 4
                px[i + 0] = b       # BGRA
                px[i + 1] = g
                px[i + 2] = r
                px[i + 3] = (hit * 255 + 8) // 16
        ii = ICONINFO()
        ii.fIcon = 1
        ii.xHotspot = 0
        ii.yHotspot = 0
        ii.hbmMask = hbm_mask
        ii.hbmColor = hbm_color
        hicon = user32.CreateIconIndirect(ctypes.byref(ii))
        return hicon or 0
    finally:
        if hbm_color:
            gdi32.DeleteObject(hbm_color)
        if hbm_mask:
            gdi32.DeleteObject(hbm_mask)
        user32.ReleaseDC(None, hdc_screen)


# ---------------------------------------------------------------------------
# 四、托盘 + 电量卡片
# ---------------------------------------------------------------------------

def _log(msg):
    """写日志到标准输出；用 pythonw 启动时 stdout 为 None，静默忽略。"""
    try:
        print("%s %s" % (time.strftime("%H:%M:%S"), msg), flush=True)
    except Exception:
        pass


# 交互诊断日志：只记托盘回调与面板显隐，异常一律吞掉，绝不影响托盘运行
DEBUG_LOG = r"D:\deepseek-harness\default-workspace\.dsh-switchman\tmp\tray_debug.log"


def _dbg(msg):
    """追加一行带时间戳的诊断日志（失败静默）。"""
    try:
        d = os.path.dirname(DEBUG_LOG)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def format_tip(reading):
    """单行状态文本（用于日志，不再作为 tooltip 显示）。"""
    if reading is None:
        return "%s：未连接" % NARI_TITLE
    if not reading.online:
        return "%s：离线（缓存 %d%% / %d mV）" % (
            NARI_TITLE, reading.percent, reading.voltage)
    return "%s：%d%%（%d mV）%s" % (
        NARI_TITLE, reading.percent, reading.voltage, reading.status_text)


class TrayApp(object):
    """托盘应用：隐藏窗口收托盘消息 + 程序绘制图标 + 点击弹出电量卡片。"""

    def __init__(self, demo=False):
        self.hwnd = 0
        self.hicon = 0
        self.icon_source = ""
        self.nid = None
        self.reading = None       # 最近一次成功读数（失败时保留，用于「上次读数」）
        self.live = False         # 最近一次轮询是否成功
        self.tracker = BatteryPercentTracker()   # 实时百分比（电压插值 + 跨会话锚点）
        self.percent_live = None  # 最近一次实时百分比（float，未取整）
        self.timer = 0
        self.poll_ok = 0          # 成功轮询次数（日志自证用）
        self.poll_fail = 0
        self.demo = bool(demo)
        self._registered = False
        self._taskbar_created = 0
        self._wndproc_cb = WNDPROC(self._wndproc)          # 必须持引用，否则被回收
        self._panel_wndproc_cb = WNDPROC(self._panel_wndproc)
        self._class_atom = 0
        self._panel_class_atom = 0

        # 卡片布局与状态
        self.panel_hwnd = 0
        self.panel_visible = False
        self.panel_w = 0
        self.panel_h = 0
        self.ui_k = 1.0
        self.layout = {}
        self._suppress_toggle_until = 0.0    # 防止「点面板外收起」后同一次点击又开
        self._last_left_at = 0.0             # 上一次左键托盘事件时间（防 down/up 双触发）
        self._icon_id = 1                    # 与 NOTIFYICONDATAW.uID 保持一致

        # 低电量预警气泡状态
        self.warn_hwnd = 0
        self.warn_visible = False
        self.warn_w = 0
        self.warn_h = 0
        self.warn_pct = None                 # 气泡里正在显示的电量
        self.warn_layout = {}
        self.warn_auto_hide_ms = WARN_AUTO_HIDE_MS
        self._warn_low = False               # 上一轮是否处于低电量（边沿检测）
        self._warn_low_run = 0               # 连续低电量采样计数（2 次确认防抖）
        self._last_warn_at = 0.0             # 上次弹出时间（重复提醒用）
        self._warn_wndproc_cb = WNDPROC(self._warn_wndproc)   # 必须持引用
        self._warn_class_atom = 0

    # ---- 窗口与图标 ----
    def create(self):
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = self._wndproc_cb
        wc.hInstance = hinst
        wc.hIcon = 0
        wc.hCursor = 0
        wc.hbrBackground = 0
        wc.lpszClassName = CLASS_NAME
        self._class_atom = user32.RegisterClassW(ctypes.byref(wc))
        if not self._class_atom:
            err = ctypes.get_last_error()
            # 1410 = ERROR_CLASS_ALREADY_EXISTS：同进程重复注册或已有实例，可继续用
            if err != 1410:
                raise OSError("RegisterClassW 失败（错误码 %d）" % err)
        # 不可见的顶层窗口，只为收托盘消息；从不 ShowWindow
        self.hwnd = user32.CreateWindowExW(
            0, CLASS_NAME, CLASS_NAME, 0, 0, 0, 0, 0, None, None, hinst, None)
        if not self.hwnd:
            raise OSError("CreateWindowExW 失败（错误码 %d）" % ctypes.get_last_error())
        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")

    def add_icon(self):
        """把程序绘制的绿点图标挂进通知区；不再设置任何 tooltip。"""
        self.hicon = make_green_dot_icon(32)
        if self.hicon:
            self.icon_source = "程序绘制绿点(32x32)"
        else:
            self.hicon = user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
            self.icon_source = "系统默认图标（绿点绘制失败）"
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(nid)
        nid.hWnd = self.hwnd
        nid.uID = self._icon_id
        nid.uFlags = NIF_MESSAGE | NIF_ICON      # 不设 NIF_TIP：没有悬停气泡
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self.hicon
        self.nid = nid
        ok = shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        _dbg("NIM_ADD 结果=%s 错误码=%d cbSize=%d(结构体=%d) hWnd=%s uID=%d "
             "uFlags=0x%x uCallbackMessage=0x%x"
             % (bool(ok), ctypes.get_last_error(), nid.cbSize,
                ctypes.sizeof(NOTIFYICONDATAW), nid.hWnd, nid.uID,
                nid.uFlags, nid.uCallbackMessage))
        if not ok:
            raise OSError("Shell_NotifyIcon(NIM_ADD) 失败（错误码 %d）"
                          % ctypes.get_last_error())
        # 切到 v4 行为：右键发 WM_CONTEXTMENU，左键发 NIN_SELECT
        try:
            ver = NOTIFYICONDATAW()
            ver.cbSize = ctypes.sizeof(ver)
            ver.hWnd = self.hwnd
            ver.uID = self._icon_id
            ver.uVersion = NOTIFYICON_VERSION_4
            okv = shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(ver))
            _dbg("NIM_SETVERSION 结果=%s 错误码=%d uVersion=%d"
                 % (bool(okv), ctypes.get_last_error(), ver.uVersion))
        except Exception as exc:
            _dbg("NIM_SETVERSION 异常：%s" % exc)

    def remove_icon(self):
        if self.nid is not None:
            try:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid))
            except Exception:
                pass
            self.nid = None
        if self.hicon:
            try:
                user32.DestroyIcon(self.hicon)
            except Exception:
                pass
            self.hicon = 0

    # ---- 电量刷新 ----
    def refresh(self, reason=""):
        """读一次电量；失败时保留上次读数并标记未连接。"""
        started = time.time()
        try:
            reading = read_battery()
        except Exception as exc:
            self.poll_fail += 1
            self.live = False
            _log("轮询失败（%s）：%s → 显示未连接，%d 秒后重试"
                 % (reason, exc, REFRESH_MS // 1000))
            self._repaint()
            self._update_warn()          # 轮询失败/离线：立即收起预警气泡
            return None
        self.reading = reading
        self.live = True
        try:
            # 实时百分比：电压插值（设备粗档位只作锚点与兜底）
            self.percent_live = self.tracker.update(reading.voltage,
                                                     reading.percent)
        except Exception:
            self.percent_live = float(reading.percent)
        self.poll_ok += 1
        _log("轮询成功 #%d：%s（%.0f ms）| 原始 byte1-2=%02x%02x "
             "byte9=%02x byte14=%d"
             % (self.poll_ok, format_tip(reading), (time.time() - started) * 1000,
                reading.raw[1], reading.raw[2], reading.raw[9], reading.raw[14]))
        self._repaint()
        self._update_warn()              # 低电量预警（边沿触发 + 5 分钟重复提醒）
        return reading

    def _repaint(self):
        """面板可见时立刻重绘（30 秒定时刷新后也能看到新数值）。"""
        if self.panel_hwnd and self.panel_visible:
            user32.InvalidateRect(self.panel_hwnd, None, False)

    # ---- 卡片布局（横 4 列 × 竖 5 行，取右下格）----
    def compute_layout(self):
        rc = wt.RECT()
        ok = user32.SystemParametersInfoW(SPI_GETWORKAREA, 0,
                                          ctypes.byref(rc), 0)
        if not ok:
            rc.left = rc.top = 0
            rc.right = user32.GetSystemMetrics(SM_CXSCREEN)
            rc.bottom = user32.GetSystemMetrics(SM_CYSCREEN)
        work_w = max(320, rc.right - rc.left)
        work_h = max(240, rc.bottom - rc.top)
        cell_w = work_w // 4
        cell_h = work_h // 5

        # 以 1920 宽屏为基准等比放大（4K 100% 缩放下字才不会显得发虚偏小）
        k = max(1.0, min(2.5, cell_w / float(BASE_CELL_W)))
        self.ui_k = k
        margin = int(round(BASE_MARGIN * k))

        W = max(240, cell_w - margin)          # 右侧留出 margin，避免与屏幕边缘贴死
        H = min(cell_h, int(round(BASE_CARD_H * k)))
        x = rc.left + 3 * cell_w               # 第 4 列左沿
        y = rc.bottom - H - margin             # 底边距工作区底部 margin
        row5_top = rc.top + 4 * cell_h
        clamped = False
        if y < row5_top:                       # 超出第 5 行就夹到行顶，宁可少贴底
            y = row5_top + max(2, margin // 2)
            clamped = True

        self.panel_w = int(W)
        self.panel_h = int(H)
        self.layout = {
            "work": (rc.left, rc.top, work_w, work_h),
            "cell": (cell_w, cell_h),
            "k": k,
            "x": x, "y": y, "w": int(W), "h": int(H),
            "margin": margin, "row5_top": row5_top, "clamped": clamped,
        }
        return self.layout

    def _metrics(self):
        """把卡片内各块的位置算成像素；全部由 k 与 H 推导，文字永远不裁。"""
        k = self.ui_k
        W, H = self.panel_w, self.panel_h

        def s(v):
            return max(1, int(round(v * k)))

        m = {
            "pad": s(16),
            "dot": s(8),
            "header_h": s(20),
            "name_px": s(13),
            "sub_px": s(11),
            "pct_px": s(28),
            "mv_px": s(12),
            "bar_px": s(12),
            "bar_h": s(30),
            "row1_y": s(16),
            "row2_y": s(38),
            "row2_h": s(40),
            "bat_w": s(44),
            "bat_h": s(22),
            "gap": s(8),
        }
        m["bar_y"] = H - m["pad"] - m["bar_h"]
        m["mid_y"] = m["row2_y"] + m["row2_h"]
        m["mid_h"] = max(s(24), m["bar_y"] - m["gap"] - m["mid_y"])
        m["bar_x"] = m["pad"]
        m["bar_w"] = W - 2 * m["pad"]
        return m

    # ---- 面板窗口 ----
    def create_panel(self):
        lay = self.compute_layout()
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = self._panel_wndproc_cb
        wc.hInstance = hinst
        wc.hIcon = 0
        wc.hCursor = 0
        wc.hbrBackground = 0
        wc.lpszClassName = PANEL_CLASS_NAME
        self._panel_class_atom = user32.RegisterClassW(ctypes.byref(wc))
        if not self._panel_class_atom:
            err = ctypes.get_last_error()
            if err != 1410:
                raise OSError("面板 RegisterClassW 失败（错误码 %d）" % err)
        self.panel_hwnd = user32.CreateWindowExW(
            WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE,
            PANEL_CLASS_NAME, "雷蛇耳机电量", WS_POPUP,
            lay["x"], lay["y"], lay["w"], lay["h"],
            None, None, hinst, None)
        if not self.panel_hwnd:
            raise OSError("面板 CreateWindowExW 失败（错误码 %d）"
                          % ctypes.get_last_error())
        radius = int(round(14 * self.ui_k))
        hrgn = gdi32.CreateRoundRectRgn(0, 0, lay["w"] + 1, lay["h"] + 1,
                                        radius, radius)
        if hrgn:
            # 区域交给窗口管理，不要再自己 DeleteObject
            user32.SetWindowRgn(self.panel_hwnd, hrgn, True)
        _log("卡片布局：工作区 %dx%d cell %dx%d k=%.2f W=%d H=%d x=%d y=%d 夹取=%s"
             % (lay["work"][2], lay["work"][3], lay["cell"][0], lay["cell"][1],
                lay["k"], lay["w"], lay["h"], lay["x"], lay["y"],
                "是" if lay["clamped"] else "否"))

    def show_panel(self, reason=""):
        if not self.panel_hwnd:
            _dbg("show_panel(%s)：panel_hwnd=0，无法显示" % reason)
            return
        lay = self.layout or self.compute_layout()
        x, y = lay.get("x", 0), lay.get("y", 0)
        w, h = lay.get("w", self.panel_w), lay.get("h", self.panel_h)
        self.panel_visible = True
        user32.ShowWindow(self.panel_hwnd, SW_SHOWNORMAL)
        # 显式置顶并给足坐标：SW_SHOWNORMAL 之后窗口可能仍被别的顶层窗口压住
        okpos = user32.SetWindowPos(
            self.panel_hwnd, wt.HWND(HWND_TOPMOST), x, y, w, h,
            SWP_SHOWWINDOW | SWP_NOACTIVATE)
        user32.InvalidateRect(self.panel_hwnd, None, True)
        user32.UpdateWindow(self.panel_hwnd)
        # 捕获鼠标：之后点面板外任意处都会作为 WM_LBUTTONDOWN 回到面板
        user32.SetCapture(self.panel_hwnd)
        rc = wt.RECT()
        user32.GetWindowRect(self.panel_hwnd, ctypes.byref(rc))
        _dbg("show_panel(%s)：SetWindowPos=%s 期望=(%d,%d,%d,%d) 实际=(%d,%d)-(%d,%d) "
             "IsWindowVisible=%s capture=%s"
             % (reason, bool(okpos), x, y, w, h, rc.left, rc.top, rc.right, rc.bottom,
                bool(user32.IsWindowVisible(self.panel_hwnd)),
                user32.GetCapture()))

    def hide_panel(self, reason=""):
        if not self.panel_visible and not (self.panel_hwnd and
                                          user32.IsWindowVisible(self.panel_hwnd)):
            _dbg("hide_panel(%s)：本来不可见" % reason)
            return
        self.panel_visible = False
        if user32.GetCapture() == self.panel_hwnd:
            user32.ReleaseCapture()
        if self.panel_hwnd:
            user32.ShowWindow(self.panel_hwnd, SW_HIDE)
        _dbg("hide_panel(%s)：已隐藏 IsWindowVisible=%s"
             % (reason, bool(self.panel_hwnd and
                             user32.IsWindowVisible(self.panel_hwnd))))

    def toggle_panel(self, reason=""):
        # 以真实可见性为准重算状态，避免 panel_visible 与窗口实际显隐不一致后卡死
        real = bool(self.panel_hwnd and user32.IsWindowVisible(self.panel_hwnd))
        if real != self.panel_visible:
            _dbg("toggle_panel(%s)：状态不一致，记录=%s 实际=%s → 以实际为准"
                 % (reason, self.panel_visible, real))
            self.panel_visible = real
        _dbg("toggle_panel(%s)：当前可见=%s" % (reason, real))
        if real:
            self.hide_panel(reason)
        else:
            self.show_panel(reason)

    def _in_bar(self, cx, cy):
        m = self._metrics()
        return (m["bar_x"] <= cx < m["bar_x"] + m["bar_w"]
                and m["bar_y"] <= cy < m["bar_y"] + m["bar_h"])

    def _on_panel_button(self):
        """面板收到鼠标按下：落在面板外就收起，落在底部紫条上就立即刷新。"""
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        rc = wt.RECT()
        user32.GetWindowRect(self.panel_hwnd, ctypes.byref(rc))
        inside = (rc.left <= pt.x < rc.right) and (rc.top <= pt.y < rc.bottom)
        _dbg("面板收到鼠标按下：光标=(%d,%d) 面板=(%d,%d)-(%d,%d) 内部=%s"
             % (pt.x, pt.y, rc.left, rc.top, rc.right, rc.bottom, inside))
        if not inside:
            # 同一次点击的抬起可能还会触发托盘通知，短暂抑制以免又弹开
            self._suppress_toggle_until = time.time() + 0.35
            self.hide_panel("点面板外")
            return
        if self._in_bar(pt.x - rc.left, pt.y - rc.top):
            self.refresh("面板")
            user32.InvalidateRect(self.panel_hwnd, None, False)

    # ---- 卡片绘制 ----
    def _paint_panel(self):
        hwnd = self.panel_hwnd
        if not hwnd:
            return
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        if not hdc:
            return
        mem = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, self.panel_w, self.panel_h) if mem else 0
        old = gdi32.SelectObject(mem, bmp) if bmp else 0
        try:
            self._draw_card(mem if bmp else hdc)
            if bmp:
                gdi32.BitBlt(hdc, 0, 0, self.panel_w, self.panel_h, mem, 0, 0,
                             SRCCOPY)
        finally:
            if bmp:
                gdi32.SelectObject(mem, old)
                gdi32.DeleteObject(bmp)
            if mem:
                gdi32.DeleteDC(mem)
            user32.EndPaint(hwnd, ctypes.byref(ps))

    def _draw_card(self, hdc):
        m = self._metrics()
        k = self.ui_k
        W, H = self.panel_w, self.panel_h
        gdi32.SetBkMode(hdc, TRANSPARENT)

        # 1) 卡片底色（窗口已被圆角区域裁掉四角）
        _fill(hdc, 0, 0, W, H, COLOR_CARD)

        r = self.reading
        online = bool(r is not None and self.live and r.online)
        coarse = r.percent if r is not None else None   # 设备粗档位（10% 一档）
        pct = self.percent_live if self.percent_live is not None else coarse

        # 2) 顶部：绿点 + 设备名 + 右侧「2.4G 无线」
        dot = m["dot"]
        dot_x = m["pad"]
        dot_y = m["row1_y"] + (m["header_h"] - dot) // 2
        brush = gdi32.CreateSolidBrush(_rgb(COLOR_GREEN))
        old_b = gdi32.SelectObject(hdc, brush)
        old_p = gdi32.SelectObject(hdc, gdi32.GetStockObject(NULL_PEN))
        gdi32.Ellipse(hdc, dot_x, dot_y, dot_x + dot, dot_y + dot)
        gdi32.SelectObject(hdc, old_p)
        gdi32.SelectObject(hdc, old_b)
        gdi32.DeleteObject(brush)

        name_x = dot_x + dot + m["gap"]
        sub_w = m["pad"] + int(round(70 * k))
        _draw_text(hdc, DEVICE_NAME, name_x, m["row1_y"],
                   W - name_x - sub_w - m["pad"], m["header_h"],
                   m["name_px"], True, COLOR_TEXT)
        _draw_text(hdc, DEVICE_SUB, W - m["pad"] - sub_w, m["row1_y"],
                   sub_w, m["header_h"], m["sub_px"], False, COLOR_SUB,
                   DT_RIGHT)

        # 3) 电量区：电池图标 + 大字号百分比 + 电压
        if pct is None:
            pct_text = "--"
            fill_pct = 0.0
            bat_color = COLOR_SUB
            pct_color = COLOR_SUB
        else:
            pct_text = "%d%%" % int(round(pct))
            fill_pct = max(0.0, min(100.0, float(pct)))   # 电池填充用未取整值
            if not online:
                bat_color = COLOR_SUB
                pct_color = COLOR_SUB
            else:
                if fill_pct >= 70:
                    bat_color = COLOR_BAT_HIGH
                elif fill_pct >= 40:
                    bat_color = COLOR_BAT_MID
                else:
                    bat_color = COLOR_BAT_LOW
                pct_color = COLOR_TEXT
        self._draw_battery(hdc, m, fill_pct, bat_color)
        bx = m["pad"]
        bw = m["bat_w"]
        nub_w = max(2, int(round(bw * 0.09)))
        pct_x = bx + bw + nub_w + int(round(12 * k))
        _draw_text(hdc, pct_text, pct_x, m["row2_y"], int(round(160 * k)),
                   m["row2_h"], m["pct_px"], True, pct_color)
        if r is not None:
            pw = _text_width(hdc, pct_text, m["pct_px"], True)
            mv_text = ("%d mV · 档 %d%%" % (r.voltage, coarse)
                       if coarse is not None else "%d mV" % r.voltage)
            _draw_text(hdc, mv_text,
                       pct_x + pw + int(round(10 * k)),
                       m["row2_y"] + m["row2_h"] // 2,
                       int(round(120 * k)), m["row2_h"] // 2,
                       m["mv_px"], False, COLOR_SUB)

        # 4) 中部：头戴耳机正视图（纯 GDI 线条，无图片）
        box = (m["pad"] + int(round(4 * k)), m["mid_y"],
               W - 2 * m["pad"] - int(round(8 * k)), m["mid_h"])
        self._draw_headphone(hdc, box)

        # 5) 底部：浅紫圆角条（状态 + 倒三角）
        bar_x, bar_y = m["bar_x"], m["bar_y"]
        bar_w, bar_h = m["bar_w"], m["bar_h"]
        radius = int(round(10 * k))
        brush = gdi32.CreateSolidBrush(_rgb(COLOR_BAR))
        old_b = gdi32.SelectObject(hdc, brush)
        old_p = gdi32.SelectObject(hdc, gdi32.GetStockObject(NULL_PEN))
        gdi32.RoundRect(hdc, bar_x, bar_y, bar_x + bar_w, bar_y + bar_h,
                        radius, radius)
        gdi32.SelectObject(hdc, old_p)
        gdi32.SelectObject(hdc, old_b)
        gdi32.DeleteObject(brush)

        if r is None:
            status = "未连接·暂无读数"
        elif not online:
            status = "未连接·显示上次读数"
        else:
            status = r.status_text
        sdot = max(4, int(round(7 * k)))
        sdot_x = bar_x + int(round(14 * k))
        sdot_y = bar_y + (bar_h - sdot) // 2
        brush = gdi32.CreateSolidBrush(
            _rgb(COLOR_BAT_HIGH if online else COLOR_BAT_LOW))
        old_b = gdi32.SelectObject(hdc, brush)
        old_p = gdi32.SelectObject(hdc, gdi32.GetStockObject(NULL_PEN))
        gdi32.Ellipse(hdc, sdot_x, sdot_y, sdot_x + sdot, sdot_y + sdot)
        gdi32.SelectObject(hdc, old_p)
        gdi32.SelectObject(hdc, old_b)
        gdi32.DeleteObject(brush)

        text_x = sdot_x + sdot + int(round(8 * k))
        arrow_w = int(round(26 * k))
        _draw_text(hdc, status, text_x, bar_y,
                   bar_x + bar_w - text_x - arrow_w, bar_h,
                   m["bar_px"], False, COLOR_BAR_TEXT)

        # 倒三角：用 Polygon 画，避免字体缺 ▾ 字形变成方框
        acx = bar_x + bar_w - int(round(16 * k))
        acy = bar_y + bar_h // 2
        aw = max(3, int(round(5 * k)))
        ah = max(2, int(round(3 * k)))
        pts = (wt.POINT * 3)(wt.POINT(acx - aw, acy - ah),
                             wt.POINT(acx + aw, acy - ah),
                             wt.POINT(acx, acy + ah))
        brush = gdi32.CreateSolidBrush(_rgb(COLOR_BAR_TEXT))
        old_b = gdi32.SelectObject(hdc, brush)
        old_p = gdi32.SelectObject(hdc, gdi32.GetStockObject(NULL_PEN))
        gdi32.Polygon(hdc, pts, 3)
        gdi32.SelectObject(hdc, old_p)
        gdi32.SelectObject(hdc, old_b)
        gdi32.DeleteObject(brush)

    def _draw_battery(self, hdc, m, fill_pct, bat_color):
        """电池图标：外框 + 按百分比填充 + 右侧正极凸点。"""
        k = self.ui_k
        bx = m["pad"]
        bw, bh = m["bat_w"], m["bat_h"]
        by = m["row2_y"] + (m["row2_h"] - bh) // 2
        pen_w = max(1, int(round(2 * k)))
        rad = max(2, int(round(5 * k)))
        nub_w = max(2, int(round(bw * 0.09)))
        nub_h = max(4, int(round(bh * 0.42)))

        # 外框
        pen = gdi32.CreatePen(PS_SOLID, pen_w, _rgb(COLOR_BAT_FRAME))
        old_p = gdi32.SelectObject(hdc, pen)
        old_b = gdi32.SelectObject(hdc, gdi32.GetStockObject(NULL_BRUSH))
        gdi32.RoundRect(hdc, bx, by, bx + bw, by + bh, rad, rad)
        gdi32.SelectObject(hdc, old_b)
        gdi32.SelectObject(hdc, old_p)
        gdi32.DeleteObject(pen)

        # 正极凸点
        nub_x = bx + bw
        nub_y = by + (bh - nub_h) // 2
        brush = gdi32.CreateSolidBrush(_rgb(COLOR_BAT_FRAME))
        old_b = gdi32.SelectObject(hdc, brush)
        old_p = gdi32.SelectObject(hdc, gdi32.GetStockObject(NULL_PEN))
        gdi32.RoundRect(hdc, nub_x, nub_y, nub_x + nub_w, nub_y + nub_h,
                        max(1, rad // 2), max(1, rad // 2))
        gdi32.SelectObject(hdc, old_p)
        gdi32.SelectObject(hdc, old_b)
        gdi32.DeleteObject(brush)

        # 电量填充
        if fill_pct <= 0:
            return
        inset = pen_w + max(1, int(round(2 * k)))
        inner_w = bw - 2 * inset
        inner_h = bh - 2 * inset
        w = int(round(inner_w * fill_pct / 100.0))
        if w < 1 or inner_h < 1:
            return
        w = max(1, min(w, inner_w))
        brush = gdi32.CreateSolidBrush(_rgb(bat_color))
        old_b = gdi32.SelectObject(hdc, brush)
        old_p = gdi32.SelectObject(hdc, gdi32.GetStockObject(NULL_PEN))
        r2 = max(1, rad // 2)
        gdi32.RoundRect(hdc, bx + inset, by + inset,
                        bx + inset + w, by + inset + inner_h, r2, r2)
        gdi32.SelectObject(hdc, old_p)
        gdi32.SelectObject(hdc, old_b)
        gdi32.DeleteObject(brush)

    def _draw_headphone(self, hdc, box):
        """中部头戴耳机正视图：头梁贝塞尔弧 + 两侧耳罩（深灰描边、浅灰填充）。"""
        bx, by, bw, bh = box
        if bw < 40 or bh < 20:
            return
        k = self.ui_k
        cx = bx + bw // 2
        cup_h = int(bh * 0.62)
        cup_w = max(10, int(cup_h * 0.66))
        cup_y = by + bh - cup_h
        span = max(cup_w, int(cup_h * 1.25))
        span = min(span, bw // 2 - cup_w)
        if span < cup_w:
            span = cup_w
        band_end_y = cup_y + max(2, int(cup_h * 0.12))
        band_top = by + max(0, int(bh * 0.03))
        line_w = max(2, int(round(3.0 * k)))

        # 头梁：三次贝塞尔，两端落在两侧耳罩顶部
        pen = gdi32.CreatePen(PS_SOLID, line_w, _rgb(COLOR_LINE))
        old_p = gdi32.SelectObject(hdc, pen)
        old_b = gdi32.SelectObject(hdc, gdi32.GetStockObject(NULL_BRUSH))
        pts = (wt.POINT * 4)(wt.POINT(cx - span, band_end_y),
                             wt.POINT(cx - span, band_top),
                             wt.POINT(cx + span, band_top),
                             wt.POINT(cx + span, band_end_y))
        gdi32.PolyBezier(hdc, pts, 4)

        # 两侧耳罩：圆角矩形 + 内圈扬声器
        cup_rad = max(4, int(cup_w * 0.55))
        for side in (-1, 1):
            x0 = cx + side * span - cup_w // 2
            brush = gdi32.CreateSolidBrush(_rgb(COLOR_CUP))
            old_bb = gdi32.SelectObject(hdc, brush)
            gdi32.RoundRect(hdc, x0, cup_y, x0 + cup_w, cup_y + cup_h,
                            cup_rad, cup_rad)
            gdi32.SelectObject(hdc, old_bb)
            gdi32.DeleteObject(brush)
        gdi32.SelectObject(hdc, old_b)
        gdi32.SelectObject(hdc, old_p)
        gdi32.DeleteObject(pen)

        # 内圈（浅灰实心小圆，模拟耳罩内部）
        d = max(4, int(cup_w * 0.42))
        brush = gdi32.CreateSolidBrush(_rgb(COLOR_CUP_INNER))
        old_b = gdi32.SelectObject(hdc, brush)
        old_p = gdi32.SelectObject(hdc, gdi32.GetStockObject(NULL_PEN))
        for side in (-1, 1):
            x0 = cx + side * span - d // 2
            y0 = cup_y + cup_h // 2 - d // 2
            gdi32.Ellipse(hdc, x0, y0, x0 + d, y0 + d)
        gdi32.SelectObject(hdc, old_p)
        gdi32.SelectObject(hdc, old_b)
        gdi32.DeleteObject(brush)

    # ---- 低电量预警气泡（主面板的「缩小版」：小电池 + 大数字 + 一行提示）----
    def _icon_rect(self):
        """取托盘图标的真实屏幕矩形；取不到返回 None。

        Shell_NotifyIconGetRect 只对已挂上的 (hWnd, uID) 有效，
        图标被折叠进溢出区时可能失败，调用方需自行兜底。
        """
        try:
            ident = NOTIFYICONIDENTIFIER()
            ident.cbSize = ctypes.sizeof(ident)
            ident.hWnd = self.hwnd
            ident.uID = self._icon_id
            rc = wt.RECT()
            hr = shell32.Shell_NotifyIconGetRect(ctypes.byref(ident),
                                                 ctypes.byref(rc))
            if hr == 0 and rc.right > rc.left and rc.bottom > rc.top:
                return (rc.left, rc.top, rc.right, rc.bottom)
            _dbg("Shell_NotifyIconGetRect 失败 hr=0x%08x" % (hr & 0xFFFFFFFF))
        except Exception as exc:
            _dbg("Shell_NotifyIconGetRect 异常：%s" % exc)
        return None

    def compute_warn_layout(self):
        """算预警气泡的屏幕矩形（尺寸、水平对齐、贴任务栏上方）。

        面积 = 主面板面积 * 0.36 * 0.28 = 0.1008（约 1/10）；
        水平中心对准托盘图标真实中心，取不到图标矩形时兜底到工作区右下；
        垂直方向底边 = 工作区底边 - 8px（紧贴任务栏上方）。
        """
        lay = self.layout or self.compute_layout()
        wl, wt_, ww, wh = lay["work"]
        W = max(120, int(round(lay["w"] * WARN_W_RATIO)))
        H = max(60, int(round(lay["h"] * WARN_H_RATIO)))
        ir = self._icon_rect()
        if ir:
            cx = (ir[0] + ir[2]) // 2
            anchor = "托盘图标中心"
        else:
            cx = wl + ww - 16 - W // 2       # 兜底：右边缘留 16px
            anchor = "兜底(工作区右下)"
        min_x = wl + WARN_EDGE_MARGIN
        max_x = wl + ww - WARN_EDGE_MARGIN - W
        x = cx - W // 2
        if max_x <= min_x:
            x = min_x
        else:
            x = max(min_x, min(max_x, x))
        y = wt_ + wh - WARN_EDGE_MARGIN - H
        self.warn_w = W
        self.warn_h = H
        self.warn_layout = {"x": x, "y": y, "w": W, "h": H, "cx": cx,
                            "anchor": anchor, "icon_rect": ir}
        return self.warn_layout

    def _warn_metrics(self):
        """气泡内部布局：上半「小电池 + 大数字」，下半提示文字，字号随 k 缩放。"""
        k = self.ui_k or 1.0
        H = self.warn_h

        def s(v):
            return max(1, int(round(v * k)))

        return {
            "pad": s(14),
            "row1_y": int(round(H * 0.10)),      # 上半段
            "row1_h": int(round(H * 0.50)),
            "row2_y": int(round(H * 0.62)),      # 下半段
            "row2_h": int(round(H * 0.30)),
            "pct_px": s(20),                     # 气泡里最醒目的字号
            "hint_px": s(11),
            "bat_w": s(26),
            "bat_h": s(13),
            "gap": s(8),
            "border": max(1, s(1)),
            "radius": max(4, s(10)),
        }

    def create_warn(self):
        """创建（隐藏的）预警气泡窗口；失败只记日志，不影响托盘主功能。"""
        try:
            lay = self.compute_warn_layout()
            hinst = kernel32.GetModuleHandleW(None)
            wc = WNDCLASSW()
            wc.style = 0
            wc.lpfnWndProc = self._warn_wndproc_cb
            wc.hInstance = hinst
            wc.hIcon = 0
            wc.hCursor = 0
            wc.hbrBackground = 0
            wc.lpszClassName = WARN_CLASS_NAME
            self._warn_class_atom = user32.RegisterClassW(ctypes.byref(wc))
            if not self._warn_class_atom:
                err = ctypes.get_last_error()
                if err != 1410:              # 1410 = 类已注册，可复用
                    raise OSError("预警气泡 RegisterClassW 失败（错误码 %d）" % err)
            self.warn_hwnd = user32.CreateWindowExW(
                WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE,
                WARN_CLASS_NAME, "低电量预警", WS_POPUP,
                lay["x"], lay["y"], lay["w"], lay["h"],
                None, None, hinst, None)
            if not self.warn_hwnd:
                raise OSError("预警气泡 CreateWindowExW 失败（错误码 %d）"
                              % ctypes.get_last_error())
            radius = max(4, int(round(10 * (self.ui_k or 1.0))))
            hrgn = gdi32.CreateRoundRectRgn(0, 0, lay["w"] + 1, lay["h"] + 1,
                                            radius, radius)
            if hrgn:
                user32.SetWindowRgn(self.warn_hwnd, hrgn, True)
            _log("预警气泡已创建：%dx%d @ (%d,%d) 锚点=%s"
                 % (lay["w"], lay["h"], lay["x"], lay["y"], lay["anchor"]))
            return True
        except Exception as exc:
            _log("预警气泡创建失败：%s" % exc)
            self.warn_hwnd = 0
            return False

    def show_warn(self, pct, auto_hide_ms=None):
        """显示预警气泡并挂上自动收起计时器；返回是否真的显示成功。"""
        if not self.warn_hwnd and not self.create_warn():
            return False
        lay = self.compute_warn_layout()
        self.warn_pct = max(0, min(100, int(round(float(pct)))))
        self.warn_visible = True
        okpos = user32.SetWindowPos(
            self.warn_hwnd, wt.HWND(HWND_TOPMOST), lay["x"], lay["y"],
            lay["w"], lay["h"], SWP_SHOWWINDOW | SWP_NOACTIVATE)
        # 重新套一次圆角区域：主面板尺寸若变化，气泡尺寸也跟着变
        radius = max(4, int(round(10 * (self.ui_k or 1.0))))
        hrgn = gdi32.CreateRoundRectRgn(0, 0, lay["w"] + 1, lay["h"] + 1,
                                        radius, radius)
        if hrgn:
            user32.SetWindowRgn(self.warn_hwnd, hrgn, True)
        user32.InvalidateRect(self.warn_hwnd, None, True)
        user32.UpdateWindow(self.warn_hwnd)
        # 只在主面板不可见时抢鼠标捕获：主面板「点外部收起」靠同一个捕获，
        # 两边同时抢会互相触发 WM_CAPTURECHANGED，故优先级让给主面板。
        captured = False
        if not (self.panel_hwnd and user32.IsWindowVisible(self.panel_hwnd)):
            captured = bool(user32.SetCapture(self.warn_hwnd))
        ms = self.warn_auto_hide_ms if auto_hide_ms is None else int(auto_hide_ms)
        user32.SetTimer(self.warn_hwnd, WARN_TIMER_ID, ms, None)
        _dbg("show_warn：pct=%d 期望=(%d,%d,%d,%d) SetWindowPos=%s 捕获=%s "
             "自动收起=%dms 锚点=%s"
             % (self.warn_pct, lay["x"], lay["y"], lay["w"], lay["h"],
                bool(okpos), captured, ms, lay["anchor"]))
        return True

    def hide_warn(self, reason=""):
        """收起预警气泡（同时杀掉自动收起计时器）。"""
        if self.warn_hwnd:
            user32.KillTimer(self.warn_hwnd, WARN_TIMER_ID)
        visible = bool(self.warn_hwnd and user32.IsWindowVisible(self.warn_hwnd))
        if not self.warn_visible and not visible:
            return
        self.warn_visible = False
        if self.warn_hwnd and user32.GetCapture() == self.warn_hwnd:
            user32.ReleaseCapture()
        if self.warn_hwnd:
            user32.ShowWindow(self.warn_hwnd, SW_HIDE)
        _dbg("hide_warn(%s)：已隐藏 IsWindowVisible=%s"
             % (reason, bool(self.warn_hwnd and
                             user32.IsWindowVisible(self.warn_hwnd))))

    def _update_warn(self):
        """低电量预警状态机：实时值 <10、连续 2 次确认、5 分钟重复提醒。"""
        r = self.reading
        online = bool(r is not None and self.live and r.online)
        # 判据用实时百分比；实时值还没有时退回设备粗档位
        pct = self.percent_live if self.percent_live is not None else (
            r.percent if r is not None else None)
        low = bool(online and pct is not None and pct < WARN_LOW_PCT
                   and r.status == STATUS_DISCHARGING)
        if not low:
            self._warn_low_run = 0
            self._warn_low = False
            self.hide_warn("电量恢复/不在放电" if online else "离线或未连接")
            return
        # 连续 2 次采样都 <10 才算真的低电量，避免 9/10 边界抖动来回闪
        self._warn_low_run += 1
        if self._warn_low_run < 2:
            _dbg("低电量待确认：%.1f%%（第 %d/2 次采样）"
                 % (pct, self._warn_low_run))
            return
        now = time.time()
        if self.warn_visible:
            # 气泡还在：只刷新数字，不重置计时器（避免每轮刷新都把 12 秒续命）
            new_pct = int(round(pct))
            if self.warn_pct != new_pct:
                self.warn_pct = new_pct
                if self.warn_hwnd:
                    user32.InvalidateRect(self.warn_hwnd, None, False)
            return
        if not self._warn_low:
            # 边沿：从 >=10 进入 <10（已连续 2 次确认）→ 立即弹
            if self.show_warn(pct):
                self._last_warn_at = now
                self._warn_low = True
                _log("低电量预警：%.1f%%（放电中）→ 弹出气泡，%d 秒后自动收起"
                     % (pct, self.warn_auto_hide_ms // 1000))
            return
        if now - self._last_warn_at >= WARN_REPEAT_S:
            # 持续低电量：距上次弹出超过 5 分钟再提醒一次
            if self.show_warn(pct):
                self._last_warn_at = now
                _log("低电量预警：持续低于 %d%% → %.0f 分钟后再次提醒"
                     % (WARN_LOW_PCT, WARN_REPEAT_S / 60.0))

    def _on_warn_button(self):
        """点气泡自身，或（捕获状态下）点气泡外部 → 收起。"""
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        rc = wt.RECT()
        user32.GetWindowRect(self.warn_hwnd, ctypes.byref(rc))
        inside = (rc.left <= pt.x < rc.right) and (rc.top <= pt.y < rc.bottom)
        _dbg("预警气泡收到鼠标按下：光标=(%d,%d) 气泡=(%d,%d)-(%d,%d) 内部=%s"
             % (pt.x, pt.y, rc.left, rc.top, rc.right, rc.bottom, inside))
        self.hide_warn("点击气泡" if inside else "点击气泡外")

    def _paint_warn(self):
        """预警气泡的双缓冲绘制（与主面板同一套 GDI 写法）。"""
        hwnd = self.warn_hwnd
        if not hwnd:
            return
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        if not hdc:
            return
        w, h = self.warn_w, self.warn_h
        mem = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h) if mem else 0
        old = gdi32.SelectObject(mem, bmp) if bmp else 0
        try:
            self._draw_warn(mem if bmp else hdc)
            if bmp:
                gdi32.BitBlt(hdc, 0, 0, w, h, mem, 0, 0, SRCCOPY)
        finally:
            if bmp:
                gdi32.SelectObject(mem, old)
                gdi32.DeleteObject(bmp)
            if mem:
                gdi32.DeleteDC(mem)
            user32.EndPaint(hwnd, ctypes.byref(ps))

    def _draw_warn(self, hdc):
        """画气泡：深色圆角底 + 细边框 +（小电池|大数字）居中 + 一行提示。"""
        m = self._warn_metrics()
        k = self.ui_k or 1.0
        W, H = self.warn_w, self.warn_h
        gdi32.SetBkMode(hdc, TRANSPARENT)

        # 1) 深色底（四角由窗口圆角区域裁掉）
        _fill(hdc, 0, 0, W, H, COLOR_WARN_BG)

        # 2) 细边框：描边色与主面板同一套常量
        pen_w = m["border"]
        pen = gdi32.CreatePen(PS_SOLID, pen_w, _rgb(COLOR_WARN_BORDER))
        old_p = gdi32.SelectObject(hdc, pen)
        old_b = gdi32.SelectObject(hdc, gdi32.GetStockObject(NULL_BRUSH))
        inset = pen_w // 2
        gdi32.RoundRect(hdc, inset, inset, W - inset - 1, H - inset - 1,
                        m["radius"], m["radius"])
        gdi32.SelectObject(hdc, old_b)
        gdi32.SelectObject(hdc, old_p)
        gdi32.DeleteObject(pen)

        pct = 0 if self.warn_pct is None else max(0, min(100, int(self.warn_pct)))
        pct_text = "%d%%" % pct

        # 3) 上半：小电池 + 大数字，作为一组水平居中
        tw = _text_width(hdc, pct_text, m["pct_px"], True)
        bw, bh = m["bat_w"], m["bat_h"]
        nub_w = max(2, int(round(bw * 0.09)))
        group_w = bw + nub_w + m["gap"] + tw
        gx = max(m["pad"], (W - group_w) // 2)
        by = m["row1_y"] + (m["row1_h"] - bh) // 2
        self._draw_warn_battery(hdc, gx, by, bw, bh, pct, COLOR_BAT_LOW)
        tx = gx + bw + nub_w + m["gap"]
        _draw_text(hdc, pct_text, tx, m["row1_y"], tw + int(round(4 * k)),
                   m["row1_h"], m["pct_px"], True, COLOR_WARN_NUM)

        # 4) 下半：提示文字，字号比数字小、颜色更浅
        _draw_text(hdc, WARN_HINT_TEXT, m["pad"], m["row2_y"],
                   W - 2 * m["pad"], m["row2_h"], m["hint_px"], False,
                   COLOR_WARN_HINT, DT_CENTER)

    def _draw_warn_battery(self, hdc, x, y, bw, bh, fill_pct, color):
        """小号电池：外框 + 正极凸点 + 按百分比的填充（沿用主面板电池画法）。"""
        k = self.ui_k or 1.0
        pen_w = max(1, int(round(1.6 * k)))
        rad = max(2, int(round(4 * k)))
        nub_w = max(2, int(round(bw * 0.09)))
        nub_h = max(4, int(round(bh * 0.42)))

        # 外框
        pen = gdi32.CreatePen(PS_SOLID, pen_w, _rgb(COLOR_BAT_FRAME))
        old_p = gdi32.SelectObject(hdc, pen)
        old_b = gdi32.SelectObject(hdc, gdi32.GetStockObject(NULL_BRUSH))
        gdi32.RoundRect(hdc, x, y, x + bw, y + bh, rad, rad)
        gdi32.SelectObject(hdc, old_b)
        gdi32.SelectObject(hdc, old_p)
        gdi32.DeleteObject(pen)

        # 正极凸点
        nub_x = x + bw
        nub_y = y + (bh - nub_h) // 2
        brush = gdi32.CreateSolidBrush(_rgb(COLOR_BAT_FRAME))
        old_b = gdi32.SelectObject(hdc, brush)
        old_p = gdi32.SelectObject(hdc, gdi32.GetStockObject(NULL_PEN))
        gdi32.RoundRect(hdc, nub_x, nub_y, nub_x + nub_w, nub_y + nub_h,
                        max(1, rad // 2), max(1, rad // 2))
        gdi32.SelectObject(hdc, old_p)
        gdi32.SelectObject(hdc, old_b)
        gdi32.DeleteObject(brush)

        # 电量填充
        if fill_pct <= 0:
            return
        inset = pen_w + max(1, int(round(2 * k)))
        inner_w = bw - 2 * inset
        inner_h = bh - 2 * inset
        w = int(round(inner_w * fill_pct / 100.0))
        if w < 1 or inner_h < 1:
            return
        w = max(1, min(w, inner_w))
        brush = gdi32.CreateSolidBrush(_rgb(color))
        old_b = gdi32.SelectObject(hdc, brush)
        old_p = gdi32.SelectObject(hdc, gdi32.GetStockObject(NULL_PEN))
        r2 = max(1, rad // 2)
        gdi32.RoundRect(hdc, x + inset, y + inset,
                        x + inset + w, y + inset + inner_h, r2, r2)
        gdi32.SelectObject(hdc, old_p)
        gdi32.SelectObject(hdc, old_b)
        gdi32.DeleteObject(brush)

    def _warn_wndproc(self, hwnd, msg, wparam, lparam):
        """预警气泡自己的窗口过程（不参与主面板逻辑）。"""
        if msg == WM_PAINT:
            self._paint_warn()
            return 0
        if msg == WM_ERASEBKGND:
            return 1                     # 全部自绘，避免闪烁
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE         # 点击也不抢焦点
        if msg == WM_TIMER:
            if int(wparam or 0) == WARN_TIMER_ID:
                self.hide_warn("自动收起")
            return 0
        if msg in (WM_LBUTTONDOWN, WM_RBUTTONDOWN):
            self._on_warn_button()
            return 0
        if msg == WM_CAPTURECHANGED:
            new_cap = int(lparam or 0) & 0xFFFFFFFF
            if self.warn_visible and new_cap != (int(hwnd or 0) & 0xFFFFFFFF):
                _dbg("预警气泡 WM_CAPTURECHANGED：新 capture=0x%x → 同步隐藏"
                     % new_cap)
                self.warn_visible = False
                if self.warn_hwnd:
                    user32.KillTimer(self.warn_hwnd, WARN_TIMER_ID)
                user32.ShowWindow(hwnd, SW_HIDE)
            return 0
        if msg == WM_DESTROY:
            self.warn_hwnd = 0
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ---- 菜单 ----
    def popup_menu(self):
        hmenu = user32.CreatePopupMenu()
        if not hmenu:
            return
        cmd = 0                          # 兜底：菜单构建中途异常时 finally 后不会 NameError
        try:
            user32.AppendMenuW(hmenu, 0x0000, CMD_REFRESH, "立即刷新")
            user32.AppendMenuW(hmenu, 0x0800, 0, None)          # MF_SEPARATOR
            user32.AppendMenuW(hmenu, 0x0000, CMD_QUIT, "退出")
            user32.SetMenuDefaultItem(hmenu, CMD_REFRESH, 0)
            pt = wt.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            # 经典组合：先置前台窗口，菜单消失后再 PostMessage WM_NULL
            user32.SetForegroundWindow(self.hwnd)
            cmd = user32.TrackPopupMenu(
                hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                pt.x, pt.y, 0, self.hwnd, None)
            user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        finally:
            user32.DestroyMenu(hmenu)
        if cmd == CMD_REFRESH:
            _log("菜单：立即刷新")
            self.refresh("手动")
        elif cmd == CMD_QUIT:
            _log("菜单：退出")
            self.shutdown()

    # ---- 收尾 ----
    def shutdown(self):
        if self.timer:
            user32.KillTimer(self.hwnd, TIMER_ID)
            self.timer = 0
        self.hide_panel()
        if self.panel_hwnd:
            user32.DestroyWindow(self.panel_hwnd)
            self.panel_hwnd = 0
        self.hide_warn("退出")
        if self.warn_hwnd:
            user32.KillTimer(self.warn_hwnd, WARN_TIMER_ID)
            user32.DestroyWindow(self.warn_hwnd)
            self.warn_hwnd = 0
        self.remove_icon()
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = 0
        _log("已退出（成功轮询 %d 次，失败 %d 次）"
             % (self.poll_ok, self.poll_fail))
        user32.PostQuitMessage(0)

    # ---- 面板窗口过程 ----
    def _panel_wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_PAINT:
            self._paint_panel()
            return 0
        if msg == WM_ERASEBKGND:
            return 1                     # 全部自绘，不要系统擦背景（避免闪烁）
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE         # 点击也不抢焦点
        if msg in (WM_LBUTTONDOWN, WM_RBUTTONDOWN):
            self._on_panel_button()
            return 0
        if msg == WM_CAPTURECHANGED:
            # 捕获被别处抢走（点外部/切窗口）时同步隐藏，避免状态卡住；
            # 若「新捕获」就是面板自己（重新 SetCapture），不算失去捕获。
            new_cap = int(lparam or 0) & 0xFFFFFFFF
            if self.panel_visible and new_cap != (int(hwnd or 0) & 0xFFFFFFFF):
                _dbg("面板 WM_CAPTURECHANGED：新 capture=0x%x → 同步隐藏" % new_cap)
                self.panel_visible = False
                user32.ShowWindow(hwnd, SW_HIDE)
            return 0
        if msg == WM_DESTROY:
            self.panel_hwnd = 0
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ---- 托盘回调解码 ----
    def _tray_event(self, wparam, lparam):
        """解出托盘回调属于哪种操作，同时兼容 V4 与旧式两种编码。

        V4（NIM_SETVERSION 生效）：lParam = MAKELONG(事件码, 图标 id)，
            事件码是 NIN_SELECT / NIN_KEYSELECT / WM_CONTEXTMENU；
        旧式（未设置或系统不支持 V4）：lParam 就是鼠标消息本身（WM_LBUTTONUP 等），
            wParam 是图标 id。
        返回 (kind, code, detail)，kind 取 "left" / "right" / None。
        """
        lv = int(lparam or 0) & 0xFFFFFFFF
        wv = int(wparam or 0) & 0xFFFFFFFF
        code = lv & 0xFFFF                     # V4 事件码 / 旧式鼠标消息
        whole = lv if lv <= 0xFFFF else None   # 旧式下 lParam 整个就是消息号
        packed_id = (lv >> 16) & 0xFFFF        # V4 高字是图标 id
        detail = ("code=0x%04x packed_id=%d wparam_lo=%d wparam_hi=%d"
                  % (code, packed_id, wv & 0xFFFF, (wv >> 16) & 0xFFFF))
        right_codes = (WM_CONTEXTMENU, WM_RBUTTONUP)
        if whole in right_codes or code in right_codes:
            return "right", code, detail
        left_codes = (NIN_SELECT, NIN_KEYSELECT, WM_LBUTTONUP, WM_LBUTTONDOWN)
        if whole in left_codes or code in left_codes:
            return "left", code, detail
        return None, code, detail

    # ---- 托盘窗口过程 ----
    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            kind, code, detail = self._tray_event(wparam, lparam)
            _dbg("托盘回调 wparam=0x%x lparam=0x%x → %s（%s）"
                 % (int(wparam or 0) & 0xFFFFFFFF, int(lparam or 0) & 0xFFFFFFFF,
                    kind or "未识别", detail))
            if kind == "right":
                self.hide_panel("托盘右键")
                self.popup_menu()
                return 0
            if kind == "left":
                now = time.time()
                if now < self._suppress_toggle_until:
                    _dbg("  左键被抑制窗口忽略（还剩 %.2fs）"
                         % (self._suppress_toggle_until - now))
                    return 0
                # down/up 成对到达时只处理一次，避免开了又立刻关
                if now - self._last_left_at < 0.25:
                    _dbg("  左键重复事件忽略（距上次 %.3fs）" % (now - self._last_left_at))
                    return 0
                self._last_left_at = now
                self.toggle_panel("托盘左键")
                return 0
            _dbg("  未识别的托盘事件，忽略")
            return 0
        if msg == WM_CONTEXTMENU:        # 某些情况下回调消息缺失，兜底
            self.hide_panel("兜底右键")
            self.popup_menu()
            return 0
        if msg == WM_TIMER and wparam == TIMER_ID:
            self.refresh("定时")
            return 0
        if msg == WM_COMMAND:
            cmd = wparam & 0xFFFF
            if cmd == CMD_REFRESH:
                self.refresh("菜单")
            elif cmd == CMD_QUIT:
                self.shutdown()
            return 0
        if msg == WM_CLOSE:
            self.shutdown()
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        if self._taskbar_created and msg == self._taskbar_created:
            # 资源管理器重启后重新挂图标
            _log("检测到 TaskbarCreated，重新添加托盘图标")
            self.hide_panel()
            try:
                self.add_icon()
            except Exception as exc:
                _log("重新添加托盘图标失败：%s" % exc)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ---- 入口 ----
    def run(self):
        self.create()
        self.add_icon()                  # 先挂图标：预警气泡要对准图标真实位置
        self.create_panel()
        self.create_warn()               # 预警气泡（创建即隐藏）
        self.refresh("启动")             # 窗口都就绪后再读，气泡可直接弹到正确位置
        _log("托盘已启动（图标来源：%s，刷新间隔 %d 秒）"
             % (self.icon_source, REFRESH_MS // 1000))
        self.timer = user32.SetTimer(self.hwnd, TIMER_ID, REFRESH_MS, None)
        if self.demo:
            _log("演示模式：直接弹出卡片（--quit 退出）")
            self.show_panel()
        msg = wt.MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        return 0


# ---------------------------------------------------------------------------
# 五、命令行入口
# ---------------------------------------------------------------------------

USAGE = """用法：razer_battery_tray.py [--once | --demo | --selftest N | --quit | --help]

  （无参数）  托盘常驻模式，左键点绿点弹出电量卡片，每 30 秒刷新一次
  --once      只读一次电量并打印结果（含电压 mV / 百分比 / 在线状态）
  --demo      启动后直接显示电量卡片（截图验收用，--quit 可关闭）
  --warn-demo [N]  伪造 N% 低电量（默认 7）直接弹出预警气泡，打印矩形/面积比/对齐
              误差/可见性，4 秒后自动收起并退出（退出码 0）
  --tracker-test  用构造数据自测实时百分比（插值/单锚点退化/clamp/持久化），不碰硬件
  --selftest N  交互自测：1=旧式左键 2=V4 左键 3=V4 右键（测完自动退出）
  --quit      通知已在运行的实例正常退出
  --help      显示本帮助
"""


def run_once():
    """--once：读一次并打印（百分比同时给实时值与设备档位），便于验收。"""
    try:
        reading = read_battery()
    except Exception as exc:
        print("状态   : 未连接")
        print("原因   : %s" % exc)
        return 1
    live = BatteryPercentTracker().update(reading.voltage, reading.percent)
    print("设备   : %s" % ("在线" if reading.online else "离线（读到的是接收器缓存值）"))
    print("电压   : %d mV" % reading.voltage)
    print("百分比 : %d%%（设备档 %d%%）" % (int(round(live)), reading.percent))
    print("状态   : %s（byte9=0x%02x）" % (reading.status_text, reading.status))
    print("原始   : %s" % " ".join("%02x" % b for b in reading.raw[:16]))
    return 0


def run_quit():
    """--quit：给运行中的实例发 WM_CLOSE，让它自己删图标并退出。"""
    hwnd = user32.FindWindowW(CLASS_NAME, None)
    if not hwnd:
        print("没有正在运行的托盘实例。")
        return 1
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    print("已通知托盘实例退出。")
    return 0


def _pump(seconds):
    """自测用的小消息循环：让面板的 WM_PAINT 等消息真正被处理。"""
    msg = wt.MSG()
    end = time.time() + seconds
    while time.time() < end:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.01)


def run_selftest(stage):
    """--selftest N：用 SendMessageW 模拟托盘回调，验证解码与面板真实可见性。

    N=1 旧式左键（wParam=图标 id, lParam=WM_LBUTTONUP）
    N=2 V4 左键（wParam=坐标, lParam=MAKELONG(NIN_SELECT, 图标 id)）
    N=3 V4 右键（lParam=MAKELONG(WM_CONTEXTMENU, 图标 id)），真弹菜单后自动关闭
    """
    app = TrayApp()
    app.create()
    app.refresh("自测")
    app.add_icon()
    app.create_panel()
    lay = app.layout
    iid = app._icon_id
    print("自测阶段 %d：面板期望位置 (%d,%d) 尺寸 %dx%d"
          % (stage, lay["x"], lay["y"], lay["w"], lay["h"]))

    def visible():
        return bool(user32.IsWindowVisible(app.panel_hwnd))

    ok = False
    if stage == 1:
        # 旧式编码：wParam=图标 id，lParam 直接是鼠标消息
        user32.SendMessageW(app.hwnd, WM_TRAY, iid, WM_LBUTTONUP)
        _pump(1.0)
        ok = visible()
        print("阶段 1 旧式 WM_LBUTTONUP：面板可见=%s" % ok)
        _pump(7.0)                      # 留给外部截屏
    elif stage == 2:
        # V4 编码：wParam=MAKELPARAM(x,y)，lParam=MAKELONG(事件码, 图标 id)
        wparam = (0 << 16) | (lay["x"] & 0xFFFF)
        lparam = (iid << 16) | NIN_SELECT
        user32.SendMessageW(app.hwnd, WM_TRAY, wparam, lparam)
        _pump(1.0)
        ok = visible()
        print("阶段 2 V4 NIN_SELECT：面板可见=%s" % ok)
        _pump(7.0)
    elif stage == 3:
        seen = []
        def _probe():
            time.sleep(2.0)
            hmenu = user32.FindWindowW("#32768", None)
            seen.append(bool(hmenu))
            _dbg("自测阶段 3：弹出菜单窗口存在=%s handle=%s" % (bool(hmenu), hmenu))
            time.sleep(5.0)
            user32.PostMessageW(app.hwnd, WM_CANCELMODE, 0, 0)
            _dbg("自测阶段 3：已发送 WM_CANCELMODE 关闭菜单")
        th = threading.Thread(target=_probe, daemon=True)
        th.start()
        user32.SendMessageW(app.hwnd, WM_TRAY, (iid << 16),
                            (iid << 16) | WM_CONTEXTMENU)
        th.join(timeout=10)
        ok = bool(seen and seen[0])
        print("阶段 3 V4 WM_CONTEXTMENU：右键菜单真弹出=%s" % ok)
    else:
        print("未知自测阶段：%d（只支持 1/2/3）" % stage)

    app.hide_panel("自测结束")
    app.remove_icon()
    if app.panel_hwnd:
        user32.DestroyWindow(app.panel_hwnd)
        app.panel_hwnd = 0
    user32.DestroyWindow(app.hwnd)
    app.hwnd = 0
    print("自测阶段 %d 结束：%s" % (stage, "通过" if ok else "未通过"))
    return 0 if ok else 1


def run_warn_demo(pct=7):
    """--warn-demo [N]：伪造低电量读数，直接弹出预警气泡并打印几何信息。

    气泡由自身的 WM_TIMER 在 4 秒后自动收起（顺带验证自动收起链路），随后退出。
    打印：气泡矩形/宽高/面积比、托盘图标真实矩形与中心 x 对齐误差、
    显示时与收起后的 IsWindowVisible。
    """
    pct = max(0, min(100, int(pct)))
    app = TrayApp()
    app.create()
    app.add_icon()
    app.create_panel()
    app.create_warn()
    # 伪造一次「在线 + 放电中 + N%」读数（不读真实 HID，保证可复现）
    raw = bytearray(64)
    raw[1], raw[2] = 0x0F, 0x05           # 链路在线
    raw[9] = STATUS_DISCHARGING           # 放电中
    raw[12], raw[13] = 0x0E, 0x74         # 3700 mV
    raw[14] = pct
    app.reading = parse(bytes(raw))
    app.live = True
    app.percent_live = float(pct)          # 实时值同样强制注入，气泡显示 N%
    app._warn_low = False
    app._warn_low_run = 0
    app.warn_auto_hide_ms = WARN_DEMO_MS
    print("伪造读数：%d%% status=0x%02x(%s) 在线=是（--warn-demo）"
          % (pct, STATUS_DISCHARGING, STATUS_TEXT[STATUS_DISCHARGING]))
    # 走真实的判据：连续 2 次采样都 <10 才弹（第一次只计数不弹）
    app._update_warn()
    app._update_warn()
    _pump(0.8)                             # 让 WM_PAINT 真正执行

    rc = wt.RECT()
    user32.GetWindowRect(app.warn_hwnd, ctypes.byref(rc))
    w = rc.right - rc.left
    h = rc.bottom - rc.top
    panel_area = float(app.panel_w * app.panel_h) or 1.0
    ratio = (w * h) / panel_area
    print("气泡矩形：(%d,%d)-(%d,%d) 宽=%d 高=%d"
          % (rc.left, rc.top, rc.right, rc.bottom, w, h))
    print("主面板：%dx%d 面积=%d"
          % (app.panel_w, app.panel_h, app.panel_w * app.panel_h))
    print("面积比：%.4f（气泡/主面板，期望 0.085-0.115）" % ratio)

    ir = app._icon_rect()
    align = None
    if ir:
        icon_cx = (ir[0] + ir[2]) / 2.0
        bubble_cx = (rc.left + rc.right) / 2.0
        align = abs(bubble_cx - icon_cx)
        print("托盘图标真实矩形：(%d,%d)-(%d,%d) 图标中心x=%.1f"
              % (ir[0], ir[1], ir[2], ir[3], icon_cx))
        print("气泡中心x=%.1f 对齐误差=%.1f px（期望 <=%d）"
              % (bubble_cx, align, WARN_ALIGN_TOL))
    else:
        print("托盘图标真实矩形：取不到（会按兜底位置居中）")

    vis_show = bool(user32.IsWindowVisible(app.warn_hwnd))
    print("显示时 IsWindowVisible：%s" % vis_show)
    _pump(WARN_DEMO_MS / 1000.0 + 1.0)     # 等气泡自己的 WM_TIMER 收起
    vis_hide = bool(user32.IsWindowVisible(app.warn_hwnd))
    print("自动收起后 IsWindowVisible：%s（%d 秒自动收起）"
          % (vis_hide, WARN_DEMO_MS // 1000))

    app.hide_warn("演示结束")
    app.remove_icon()
    if app.warn_hwnd:
        user32.DestroyWindow(app.warn_hwnd)
        app.warn_hwnd = 0
    if app.panel_hwnd:
        user32.DestroyWindow(app.panel_hwnd)
        app.panel_hwnd = 0
    user32.DestroyWindow(app.hwnd)
    app.hwnd = 0

    ok_ratio = 0.085 <= ratio <= 0.115
    ok_align = (align is None) or (align <= WARN_ALIGN_TOL)
    ok_vis = bool(vis_show) and not vis_hide
    print("检查：面积比=%s 对齐=%s 可见性=%s"
          % ("通过" if ok_ratio else "未通过",
             "通过" if ok_align else "未通过",
             "通过" if ok_vis else "未通过"))
    return 0 if (ok_ratio and ok_align and ok_vis) else 1


def run_tracker_test():
    """--tracker-test：用构造数据验证实时百分比估计（不碰硬件）。

    覆盖：双锚点分段线性插值、单锚点默认斜率退化、越界 clamp、
    空锚点退回设备档位、EMA 平滑与 JSON 锚点持久化。
    """
    path = os.path.join(tempfile.gettempdir(), "ez2.4g_tracker_test.json")
    try:
        os.remove(path)
    except OSError:
        pass
    t = BatteryPercentTracker(path)
    cases = []

    # 1) 双锚点分段线性插值
    t.anchors = {30: 3560.0, 20: 3510.0}
    cases.append((3560, 30, t.estimate(3560, 30)))
    cases.append((3535, 30, t.estimate(3535, 30)))
    cases.append((3510, 20, t.estimate(3510, 20)))
    # 2) 只有 1 个锚点：默认 50mV / 10% 估算
    t.anchors = {30: 3560.0}
    cases.append((3540, 30, t.estimate(3540, 30)))
    # 3) 越界 clamp：电压骤降也不低于 coarse-9，也不高于设备档位
    t.anchors = {30: 3560.0, 20: 3510.0}
    cases.append((3400, 30, t.estimate(3400, 30)))
    cases.append((3600, 30, t.estimate(3600, 30)))
    # 4) 空锚点：退化为设备档位
    t.anchors = {}
    cases.append((3560, 30, t.estimate(3560, 30)))

    for mv, coarse, val in cases:
        print("mv=%d coarse=%d%% -> %d%%（%.2f）"
              % (mv, coarse, int(round(val)), val))

    # 5) EMA 平滑 + 持久化：同一档位连续两次采样，锚点按 0.7/0.3 平滑后落盘
    t.anchors = {}
    v1 = t.update(3560, 30)
    v2 = t.update(3520, 30)          # 锚点 = 3560*0.7 + 3520*0.3 = 3548
    reloaded = BatteryPercentTracker(path)
    saved = reloaded.anchors.get(30)
    print("update(3560,30) -> %d%%；update(3520,30) -> %d%%" % (
        int(round(v1)), int(round(v2))))
    print("落盘锚点 anchors[30]=%s（期望 3548.0）" % saved)

    checks = [
        ("mv3560/档30 -> 30", abs(cases[0][2] - 30) < 1e-6),
        ("mv3535/档30 -> 25", abs(cases[1][2] - 25) < 1e-6),
        ("mv3510/档20 -> 20", abs(cases[2][2] - 20) < 1e-6),
        ("mv3540/档30 -> 26 附近", abs(cases[3][2] - 26) <= 1.0),
        ("骤降不低于 21（coarse-9）", cases[4][2] >= 21 - 1e-6),
        ("不高于设备档 30", cases[5][2] <= 30 + 1e-6),
        ("空锚点退回 30", abs(cases[6][2] - 30) < 1e-6),
        ("锚点持久化 3548", saved is not None and abs(saved - 3548.0) < 0.01),
    ]
    ok = True
    for name, passed in checks:
        print("检查 %-26s : %s" % (name, "通过" if passed else "未通过"))
        ok = ok and passed
    try:
        os.remove(path)
    except OSError:
        pass
    print("实时百分比自测：%s" % ("全部通过" if ok else "有未通过项"))
    return 0 if ok else 1


def main(argv):
    args = argv[1:]
    if "--help" in args or "-h" in args or "/?" in args:
        print(USAGE)
        return 0
    if "--once" in args:
        return run_once()
    if "--quit" in args:
        return run_quit()
    if "--warn-demo" in args:
        idx = args.index("--warn-demo")
        pct = 7
        if idx + 1 < len(args) and args[idx + 1].isdigit():
            pct = int(args[idx + 1])
        return run_warn_demo(pct)
    if "--tracker-test" in args:
        return run_tracker_test()
    if "--selftest" in args:
        idx = args.index("--selftest")
        stage = 1
        if idx + 1 < len(args) and args[idx + 1].isdigit():
            stage = int(args[idx + 1])
        return run_selftest(stage)
    demo = "--demo" in args
    unknown = [a for a in args if a.startswith("-") and a != "--demo"]
    if unknown:
        print("未知参数：%s" % " ".join(unknown))
        print(USAGE)
        return 2
    app = TrayApp(demo=demo)
    try:
        return app.run()
    except Exception as exc:
        _log("托盘启动失败：%s" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
