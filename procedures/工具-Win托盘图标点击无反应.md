# 工具-Win托盘图标点击无反应

> **触发**：自己写的 Windows 托盘程序（Shell_NotifyIcon / pystray / ctypes 均可）右键菜单正常、但左键点了不弹窗、弹一次后再也弹不出来、或用 SendMessage 模拟能弹真人点不弹。

## 首选姿势（可直接抄）

1. **先证明问题在哪一层，不要先猜消息编码。** 造一个独立验证脚本（见下方「验证方式」），用真实鼠标输入点一次，同时打印「点击前/后 `IsWindowVisible`」。这一步取代「等用户手点」的来回。
2. **显隐状态不许自己记账**：一律以 `IsWindowVisible(hwnd)` 为唯一真相。`self.panel_visible` 这类标志只能由真实可见性反推，否则一旦失同步就永久不弹。
3. **每次显示都强制置顶 + 强制重绘**，别只信创建时的 `WS_EX_TOPMOST`：
   `ShowWindow(hwnd, SW_SHOWNOACTIVATE)` → `SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_SHOWWINDOW|SWP_NOACTIVATE)` → `InvalidateRect(hwnd, None, False)` + `UpdateWindow(hwnd)`。
4. **按下和抬起都收**：托盘回调里同时接受 `WM_LBUTTONDOWN`、`WM_LBUTTONUP`、`NIN_SELECT`、`NIN_KEYSELECT`，右键收 `WM_CONTEXTMENU`/`WM_RBUTTONUP`，down+up 之间做 ~250 ms 去重。
5. `NIM_SETVERSION` 的返回值和 `GetLastError` 写进日志——版本没设成会让消息编码整段错位，而且窗口回调不报错。

## 反面清单（试过不通）

- ❌ **先怀疑 NOTIFYICON_VERSION_4 的编码不匹配**。实测老代码 `lParam & 0xFFFF` 在 V4 和 legacy 下都能取到事件码，编码不是根因；为此改一圈纯属白烧一轮。
- ❌ 只用 `SendMessageW` 模拟 `WM_LBUTTONUP` 就算验收。模拟消息能过、真人点击不过，因为真实点击走的是 shell 的回调通知路径（`wParam` 打包坐标、`lParam=MAKELONG(event, id)`），并且可能被 window region / 置顶层级 / 捕获状态吃掉。
- ❌ `show_panel()` 开头 `if self.panel_visible: return`。
- ❌ 不重置捕获状态：`WM_CAPTURECHANGED` 里见消息就隐藏面板，会把「自己 SetCapture 后被系统收走」也当成点外面。

## 验证方式（怎么确认真成了）

`.dsh-switchman/tmp/real_click_test.py` 的思路，四步都要打印：

1. `Shell_NotifyIconGetRect(NOTIFYICONIDENTIFIER{hWnd, uID})` 拿图标**真实屏幕矩形**（`hr=0` 且宽高 ≥4 才算可用；返回 (0,0,0,0) 说明图标在溢出区/不可点）。
2. 记录原光标 → `SetCursorPos(中心)` → `mouse_event(LEFTDOWN|LEFTUP)`。
3. `EnumWindows` 按 PID + 窗口类名找面板窗口，断言 `IsWindowVisible` 由 **False → True**。
4. 再点一次断言 **True → False**（顺带验证开关），`SetCursorPos` 还原光标。

真点一次比等用户点一次便宜，而且能顺手证明「关得掉」，不留脏状态。
