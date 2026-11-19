# 踩坑收件箱

踩到坑的**当下**往这里记一行，不等收尾 —— 回忆不可靠，当下记不会忘。
**记一行也算数**，没想清楚也先写。

一行格式：`现象（任务里会出现的说法）→ 一句话解法`

收尾时看一眼：够格的（复发的 / 代价大的）照 `_模板.md` 整理成正式卡，删掉这行；小意外留着。

---

- 从 GitHub 下 release 资产超时（`github.com` 被墙）→ `api.github.com` 和 `raw.githubusercontent.com` 仍通；改用 API 资产端点 `https://api.github.com/repos/<owner>/<repo>/releases/assets/<id>` + 请求头 `Accept: application/octet-stream`，再用 `assets[].digest` 校验 sha256。
  （2026-10-19 装 bsk CLI 时踩到，第 1 次；注意：PS 5.1 默认 TLS 1.0 **不是**本例原因，加 TLS1.2 无效，别被带偏）
- 升级已装插件时 `plugin_manager install_bundle` 直接报 `already-installed` → 得先 `remove_bundle <包名>` 再 `install_bundle <包名>@<版本>`；工具不接受覆盖式升级，返回里的 `nextStep` 已写明。
  （2026-10-19 升级 dshmarket 1.66.28 → 1.66.42，第 1 次）
- 长会话中途切到贵模型（flash → v4-pro），一轮就花 4.77 元 → 原因是**切模型使整段历史的 KV cache 失效**，全部按新单价重算一遍，不是干了多少活。要在贵模型上干活，应**新开一个短会话**、从第一句就用它，且只读落盘文件而非整段历史。
  （2026-10-20 实测：Pro 单轮 1,014,279 tokens / 4.77 元；同会话 Flash 单轮 0.03–0.81 元）
- 派子代理报 `model "deepseek-flash" does not support reasoning effort "medium"` → switchman 泳道表写的 effort（economy=medium）不是模型真实支持列表；flash 实际只支持 `off/low/high/max`。先 `list_subagent_models` 查该模型的 Reasoning efforts 再填，或干脆不传 provider/model/reasoning_effort 三字段走继承。
  （2026-10-20 派 economy 池自检时踩到，第 1 次；**2026-10-21 第 2 次**——economy 池标 medium，照表传 `reasoning_effort: "medium"` 又被拒；正确姿势是三字段全不传（或先 `list_subagent_models` 查），去掉 effort 后同一 flash 路由子代理立即派出成功。`started subagent` 只代表接受入队，不代表 effort 合法，报错是即时返回的）
- Windows 上跑 `node --check` / `git` 报「无法将...识别为 cmdlet」：PATH 里没有 node/git，直接用绝对路径 `E:\dsh-home\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe`（本机无 git CLI）。
- [2026-10-21] [dsh-longtou 面板] 现象：改了 panel.js / 重启面板服务后，面板仍显示旧样式（用户反馈「重启了没有改回来」）。解法：**同一个 8765 端口上残留了 8 个 panel_server 进程**（`.venv\Scripts\pythonw.exe` shim + 它拉起的 dsh-runtimes pythonw 子进程，每启动一次多一对，SO_REUSEADDR 让它们都能 bind），浏览器/`Invoke-WebRequest` 随机命中旧实例，于是旧 INDEX 与旧 JS 一直生效。定式：`Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" | ? { $_.CommandLine -like '*panel_server.py*' }` 全杀 → 只起一个 → `Get-NetTCPConnection -LocalPort 8765 -State Listen` 只剩一对 → 再把 HTML 里写死的 `?v=` 换成 panel.js 的 mtime 动态版号。验证：抓 `/` 断言 `panel.js?v=<纯数字>` 且不含旧字面量。
- [2026-10-21] [Windows/PS] 现象：`& .\tools\open_panel.ps1` 报「因为在此系统上禁止运行脚本」(UnauthorizedAccess/PSSecurityException)。解法：本机 ExecutionPolicy 限制，`& script.ps1` 会被拦；用 `powershell -ExecutionPolicy Bypass -File script.ps1`，或直接在 pwsh 里内联等价命令（Start-Process pythonw + Start-Process 浏览器 --app）。
- [2026-10-21] PowerShell 调 headless Chrome：Start-Process -ArgumentList @(...) 数组传参 → chrome_main.cc 报 Multiple targets are not supported in headless mode（--headless=new 被拆成两个目标），dump-dom 输出 0 字节。解法：整串参数拼成单个字符串传 -ArgumentList（含空格路径用双引号包住），并用 -RedirectStandardOutput 收 DOM。
- eltdx 竞价未匹配量是无符号绝对值：`AuctionPoint.unmatched_volume` 不含方向，方向在 `unmatched_signed_raw`/`unmatched_direction_raw`（负=卖盘）。只用绝对值会把卖盘残余当「封单」，算出 0.00% 这种假封流比（20261021 002929）；判定方向后再算。
- 子代理会话记录当探针被打爆：用 pwsh ConvertFrom-Json 读 $DSH_HOME\storages\session_projcache\sessions\<id>.json 会把整份记录（含 spans/timing）吐成几万字符。解法：写 .dsh-switchman\tmp\childprobe.py，只取 record.rows 的 openStep/lastStepBoundary/age/draft，一行输出。
- Python 脚本含 ✓/中文遇 GBK 控制台崩溃（UnicodeEncodeError: 'gbk' codec）：python x.py 直接跑会炸。解法：$env:PYTHONIOENCODING='utf-8' 且用 runpy + TextIOWrapper(encoding='utf-8', errors='replace') 包 stdout；或脚本里只用 ASCII 标记。
- [2026-10-21] [GitHub 仓库「没 release」误判] 现象：`api.github.com/repos/<o>/<r>` 返回 `has_downloads: false`、`size: 245KB`，就以为没有 exe 只能源码跑。解法：`has_downloads` 只表示 Downloads 页签关闭，release **照常有**——查 `repos/<o>/<r>/releases`，`assets[].browser_download_url` 里的 exe 10.8MB 就在那儿；下载用 `repos/<o>/<r>/releases/assets/<id>` + `Accept: application/octet-stream`（`browser_download_url` 走 github.com 会失败）。zipball 同理：`repos/<o>/<r>/zipball/<branch>` 走 api 域名可下。
- [2026-10-21] [EZ2.4G 误判「已识别两台设备」] 现象：`外设电量.exe --once` 打印两行 `：未连接（已临时忘记该设备）`，看着像工具记住了两台设备。真相：那是作者留在源码/发布版里的**测试桩**（`my_devices.py` 919–965 行附近，注释写「测试：临时忘记…」），设备名为空。判断「有没有识别到我的设备」不能数 `--once` 行数，要看 `--list` 的 VID:PID 是否落在收录表（ATK 373B/3554、MCHOSE 3837/1837/3654）。
- [2026-10-21] [雷蛇 Nari Ultimate 电量读成缓存] 现象：按 `razer-nari-linux` 的协议用 64B feature report 发出 `ff 0a 00 fd 04 12 f1 02 05`，拿回 3488mV/10%，但改 txid 字节、90s 采 6 次，响应**逐字节不变**，且 byte1-2=`0100`（规格里=离线）→ 读到的是耳机**关机时接收器缓存的旧值**。纠错点：该接口唯一 feature ReportID 是 `0xFF`（不是规格字面外的 0x09/0x00，传错 err=87）；路径必须 `HidD_SetFeature→HidD_GetFeature`（output report WriteFile 走不通，err=31）。判定「实时」的最简自检：让被测耳机/鼠标**开机**后重读，数值变了才是实时。
- [2026-10-21] [附件图片] 现象：截图是 `.avif`（路径里带 `@176w_176h_1c_1s.avif` 后缀），`read_image` 直接报「.avif extension does not declare a supported image format」，只认 PNG/JPEG/WebP/GIF。解法：本机 Pillow 12.3.0 已带 AVIF 解码（`features.check("avif")`=True），用 bundled python 一次性 `Image.open(src).convert("RGB").save(dst.png)` 转出 PNG（必要时 LANCZOS 放大 4-6x + 提高对比度再读）；176px 的小图即便放大也基本不可辨认，先按布局/配色给结论，别硬猜文字。
- [2026-10-21] ctypes 判断 Windows 句柄无效：`h == INVALID_HANDLE_VALUE` 恒为 False（c_void_p 与 int 身份比较），`not h` 也拦不住 -1（bool(c_void_p(-1)) 为 True）。正确写法 `getattr(h,"value",h) == INVALID_HANDLE_VALUE`。
- [2026-10-22] [dsh-longtou 面板 CDP 截图] 现象：按 `document.querySelector('.lt-cycle-part')` 截「情绪波浪/梯队」区，抓到的是第一个 part（情绪指数 92px 高）。解法：先取 `document.querySelector('.lt-cycle-vp').closest('.lt-cycle-part')` 再算 clip；clip 的 x 从 0 起、width 取 `min(innerWidth, rect.right)`，否则横向滚出视口的负 left 会被裁。
- [HID 探测] 键盘类集合路径带 \kbd：GENERIC_READ 报 err=5、退到 0 权限后 HidD_GetFeature 报 err=31；真因是被 kbdclass 独占。判断 feature 通道前要按 (权限高→低)×(原路径/去 \kbd) 全组合试，否则把"权限不足"误判成"设备不应答"。探针：.dsh-switchman\tmp\probe_kb_mouse.py
- [2026-10-22] 独立/浮窗页面「我加了 !important 还是不生效」：先确认被覆盖的是 CSS 自定义类规则还是**元素内联 style**。浮窗几何(left/top/width/height)常由 JS 写进 element.style，class 选择器天然打不过 → 覆盖块每条属性都要 !important，且别把规则限死在 `.open` 上（关态也要独立铺满）。验证：CDP 里读 `getComputedStyle(el).width` 与 `innerWidth` 比、并打印 `el.getAttribute("style")`。
- 字面量补丁脚本按「命中次数必须=1」校验很值：同一句文本常在注释和代码里各出现一次（本例 `2→3 / 3→4 / 4→5` 命中 2 次），用上下文锚点限定唯一性，别改成 replace-all。
- [2026-10-22] [dsh-longtou 面板截图 亮色主题] 现象：URL 加 `?theme=light` 截图还是暗色。解法：面板主题只看 `<html data-theme>` + localStorage 键 `dsh-longtou-theme-v1`（`applyTheme()` 只写这两处，没有 URL 参数入口）；CDP 里先 `localStorage.setItem('dsh-longtou-theme-v1','light')` 再 `Page.reload`，或直接 `document.documentElement.dataset.theme='light'`。另注意 `.lt-cycle-scroll` 渲染后被钉到最右（`scrollLeft=scrollWidth`），整页截图会裁掉最早几列，要全 20 列得先把 scrollLeft 归零。
- 改 A 股面板定时：DSH 提醒是会话级、存在 E:\dsh-home\storages\schedule.json，子智能体 schedule_* 直接被拒 → 先 Select-String 那个 json 看真实现有时点，落地改用 Windows 计划任务 Register-ScheduledTask -Force（4 个 -Weekly 触发器共用一个动作，阶段在脚本里按钟点判定）；NextRunTime 会带注册时的秒数，属正常。
- [2026-10-22] dsh-longtou 面板「连板梯队」不是 `<table>`：它是 `.lt-cycle-vp > .lt-cycle-ladder` 里 20 张 `.lt-cycle-col` 卡片列（`lib/panel.js:1898-1899`）。按 `table/thead` 找它必然找不到，用 `.lt-cycle-ladder .lt-cycle-col` / `.lt-cycle-col-d` 定位。
- [2026-10-22] dsh-longtou 目录下 `package.json` 有 `"type":"module"`，临时 Node 脚本用 `.js` 会报 `require is not defined`；写 `.cjs` 即可。
- [2026-10-22] `python -m unittest discover -s <项目>\tests -t <项目绝对路径>` 报 `ImportError: Start directory is not importable`（tests 下没有 `__init__.py` 时 -t 会要求可导入包）→ 正确姿势是 `cd` 到项目根后 `-m unittest discover -s tests`，**不要传 -t**。
- [2026-10-22] PowerShell 里跑 python 单测明明 `Ran N tests ... OK` 却 `[exit code: 1]` → unittest 的进度点写到 stderr，PS 把它当 NativeCommandError 抬成失败；**判断通过看 stdout 尾部的 `OK` / `FAILED`，别看 $LASTEXITCODE**（或 `2>&1 | Select-Object -Last 5` 后读文本）。
- [2026-10-22] 截图天梯时先 `vp.scrollTop=0` 再滚窗口：对 2500px 高的 `.lt-cycle-ladder` 直接 `scrollIntoView` 会把 `.lt-cycle-vp` 内部 scrollTop 居中，把大号板数和第一数据行滚出可视区。

- [2026-10-22][网页抓取] raw.githubusercontent.com 直接 web_fetch 报 fetch failed；github.com 网页抓取又会把导航栏/侧边栏一起吞进上下文（几千 token 噪音）。解法：优先 cdn.jsdelivr.net/gh/<user>/<repo>@<branch>/<path> 镜像，或 github.com 加 ?plain=1；再不行就派子代理去读，正文只回摘要。
  （2026-10-22 补充实测：jsDelivr 现在会 302 回 raw.githubusercontent.com，被 web_fetch 以 cross-origin redirect 拒绝，镜像路不通；`github.com/<o>/<r>/blob/...` 页只剩导航噪声、正文被截断；**raw 原始 URL 重试一次常常能成**（首次 fetch failed、隔一轮再抓 HTTP 200）。github.com/search 仓库检索页同理只回导航噪声，判「有没有相关仓库」别依赖它）

- [2026-10-22][数据可得性] 指南针「活跃市值」= 指标代号 0AMV（活筹指数）：官方数值拿不到（专用通讯协议 + 盘后有偿数据接收 + 指标本地计算，导出只导指标文件不导数值），社区无公开接口。自算只能近似（官方算法未公开，民间公式参数互不一致）。第三方仅猫爪数据 theme_daily 有「活筹市值」字段但无同源证据。别再重复调研，结论已存 .dsh-switchman/tmp/active_mv_probe.md。
- 2026-10-22 PowerShell 里 `&  -c ` 传 here-string 写 python 单行脚本会被吞引号（`r\"E:\...\"` 变成 `rE:\...`）→ 改法：把脚本 write 成 .py 文件再跑，别用 -c 传多行代码。
- [2026-10-22][CDP 探针] 现象：先在 `Runtime.evaluate` 里定义辅助函数 `parseD`，`Page.navigate` 之后再用就报 `ReferenceError: parseD is not defined`。解法：导航会新建 execution context、旧全局被清空；**注入必须在 navigate + 等待渲染之后再执行**。另：核对元素文案时别 `[...document.querySelectorAll('div')].map(e=>e.textContent)` 把祖先节点算进去，一匹配正则就把整页正文回传，几万字符直接冲掉输出；只取叶子节点（`childElementCount===0`）或 `closest` 定位到目标行。
- [2026-10-25][dsh-longtou 面板无法重启] 现象：点面板「重启」后服务彻底消失、面板打不开，`data/panel_restart.log` 不更新（停在几天前那条 ok）。已修 `tools/panel_server.py` 重启链路（日志新增 old_pid/new_pid，另有 `data/panel_restart_child.log`）。验证定式：`POST /restart` → 8s 内 `/health` 的 pid 必须变 → `panel_restart.log` 写 `ok:true` 且 new_pid == 端口持有者 → 30s 后 `/` 仍 200。附坑：**用 shell `Start-Process` 起的 8765 服务会随该条命令结束被清掉**（看着起成功了，命令一完就没了）；要持久必须 `Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=...; CurrentDirectory=...}`。
- [2026-10-26 更正] 下面这条 2026-10-25「多层 launcher 嵌套、每层都执行一遍 `--restart-server`」的根因判断是**错的**，勿照抄。child log 里那 4 个 pid 实为「老实例壳 + real」和「辅助进程壳 + real」两套，不是多层各跑一遍。真实根因：`.venv\Scripts\python.exe` 是**启动器壳**（CreateProcess 出真实解释器后等它退出），监听端口的是 real，**杀壳会连带杀掉 real**；旧代码只排除 `os.getpid()`，把辅助进程自己那条链的壳列进受害者 → 自杀，症状正是 exit=1、stdout 全空、无 traceback、日志不更新、老实例已死新实例没起。定式：受害者 = panel_server 进程且命令行**不含** `--restart-server`；成功判据取 `/health` 报出新 pid，不是只看端口能响应。并发误点两次重启用命名互斥体 `Global\dsh-longtou-panel-restart` 兜底（`CreateMutexW` + `get_last_error()==183` 判重；**别用锁文件**——辅助进程被 taskkill 时锁文件残留，会把之后的重启永久卡死）。验证定式：关掉面板窗口后 `POST /restart`，`/health` 的 pid 必须变，且**再等 40s 仍不变**才算过（这是之前误判 ok 的漏点）。
- [2026-10-25 修正] 上一条「面板重启已修」不成立：面板里的重启按钮仍会「日志写 ok:true、几秒后服务却消失」。根因：`.venv\Scripts\python.exe` 是多层 launcher 嵌套（`data\panel_restart_child.log` 里每次重启的进程链都是 4 层：shim→shim→真实解释器→restart 子进程），**每一层都执行了一遍 `--restart-server` 分支**，互相 taskkill/Popen；且成功判定只看端口能否响应，新实例随后被别层杀掉也报 ok。实用替代：别用面板内重启按钮，改用 `启动独立面板.cmd`（open_panel.ps1，端口被占时新实例 bind 失败退出，无害）。彻底修需加防重入哨兵（或把重启逻辑移出服务进程）。
- [2026-10-26 黑窗] 面板每重启一次就冒出一个标题为 `...\.venv\Scripts\python.exe` 的黑色控制台窗口 → 不是服务需要窗口，是启动时用了控制台子系统 python.exe；换同目录 pythonw.exe（GUI 子系统）即无窗口。open_panel.ps1 本来就优先 pythonw.exe，所以黑窗往往是自己手工用 python.exe 起实例造成的；已给 panel_server.py 加 `_launch_exe()`，重启/置顶拉起的子进程一律改用 pythonw.exe。验证：`Get-Process -Name python,pythonw | Select Id,MainWindowTitle` 全为空 + `/health` 的 pid 进程名是 pythonw.exe。
- [2026-10-26 黑窗2] 双击 `.cmd` 启动哪怕服务用 pythonw 仍会闪一下 cmd/powershell 控制台窗口 → 用 VBScript 入口（`启动独立面板.vbs`，由 wscript.exe 执行，`sh.Run "powershell ... -WindowStyle Hidden", 0, False`）可零窗口；`.cmd` 保留但改成通配转发到同目录 vbs；开始菜单 .lnk 的 TargetPath 直接指向 wscript.exe（参数为 vbs 路径）。验证：杀实例后用 `Start-Process wscript.exe -ArgumentList //nologo,vbs` 冷启动，400/900/1600/3000ms 采样 cmd/powershell/wscript/conhost 的 MainWindowTitle 全为空，且 /health 恢复、监听进程名 pythonw.exe。注意 vbs 文件必须纯 ASCII（wscript 按 ANSI 读，中文注释可能报错）。
- [2026-10-26 面板卡死/刷新不了] 现象：面板窗口还开着，点刷新/刷新数据没反应（用户口语「又卡了」）。根因基本是 8765 服务进程已**静默退出**：Get-NetTCPConnection -LocalPort 8765 -State Listen 无结果、curl.exe -s --noproxy "*" http://127.0.0.1:8765/health 空。修法：Start-Process -FilePath .venv\Scripts\pythonw.exe -ArgumentList @('tools\panel_server.py','--port','8765') -WorkingDirectory <dsh-longtou> -WindowStyle Hidden，然后面板 Ctrl+R。（open_panel.ps1 会顺带再开一个窗口，已有窗口时直接起服务即可，端口被占时新实例自行退出。）判据：/health 返回 ok 且 pid == 端口持有者，/、/panel.js、/cycle.json 全 200。注意别把「数据日期是 T-1」误判为卡死：/cycle.json 的 date 在两个采集点（11:30/13:00）本来就用上一交易日，output/panel_refresh.log 里 	arget=<T-1>；只有服务无监听才是真卡。Event Log 里查不到 pythonw 崩溃记录，属静默退出，所以没有 crash dump 可看。

[2026-11-01 cloudflared 下载慢/限时公网窗口] GitHub 直连几十 KB/s，改 https://gh-proxy.com/<github url> 用 curl.exe 10 秒下完；快速隧道要 kill cloudflared.exe（job）才能删/换文件；到期自动拆隧道用 Register-ScheduledTask -Once -At（schtasks 的 /SD 吃本地日期格式，别硬编码）。
- daily_overlay.json ȫ����д�������ݣ�eltdx ����ʧ��ʱ�ɽű��ÿ� bars ���� 4.5MB ��Ч overlay�����ձ���ȫ�ա��ⷨ���� (code,date) �ϲ����ļ� + tmp+os.replace ԭ��д + �¸��� <50% �ܾ�д�� exit 1��09:25 �׶�ҲҪˢһ����������
- dsh-longtou 面板情绪波浪/梯队只显示最近 20 天：不是数据缺失，是两处窗口常量——`longtou/cycle_source.py` 的 `build_ladder(limit=20)`（调用处传 `len(series)`）与 `lib/panel.js` 的 `MAXCOLS = 20`；两处一起改成 120 交易日后改数据即可，面板无需重启。
- [2026-11-06 pwsh 里 python -c 静默失败] 现象：在 pwsh 里写 `python -c "多行/多引号代码"` 无输出、exit code 1，看不到任何报错，误以为逻辑没跑。解法：把代码写成 .py 文件再 `python 文件.py` 跑；排查脚本一定落文件，别在命令行拼多行 python。
- [2026-11-06 子脚本 import longtou 失败] 现象：脚本放 .dsh-switchman/ 下运行报 `ModuleNotFoundError: No module named 'longtou'`。解法：`$env:PYTHONPATH='D:\deepseek-harness\default-workspace\dsh-longtou'` 后再跑，或把 cwd 切到项目根。
- 2026-11-06 pwsh 里字符串内嵌 $(...) 再带正则转义（如 "x=$(... \()" ）会抛 IncompleteDollarSubexpressionReference → 改成字符串拼接 + 变量分行赋值，别在双引号里套 $( 和圆括号。
- 2026-11-07 [handover/子代理存活] 交接（handover）后 `list_agents` 里看不到 subagent（那是 teammate 列表），别当成"死了"重派：用 `E:\dsh-home\storages\session_projcache\sessions\<id>.json` 的 LastWriteTime 判活（十几秒内还在涨=在跑），并用 `child_message(childId=..., content=...)` 补要求/收结论。
- [2026-11-08] 现象：同一模块里后定义的_TAIL_BARS=12覆盖了前一个=300，量比历史 107/120 天全是空。解法：`grep -n "常量名"` 全文件核对是否有同名重定义；改完必须重跑数据生成脚本 + POST /restart 才生效（Python 进程里是旧模块）。
- [2026-11-08] 校验脚本爱骗自己：用 `[x]*n` 造 dict 键会塌缩成唯一值、且按日历日生成 N 天会跨年改变"基准年" → 断言失败先怀疑测试数据，用日历日生成唯一键并把跨年单独列一条用例；pwsh 里 `python -c "..."` 内层双引号被吞（SyntaxError），一律落盘成 .py 再跑。
- 2026-11-08 dsh-longtou「当日列涨跌停家数缺值」：查了 3 轮才确认真因不是缺数据源——market_stats.json 当日有 limit_up、/cycle.json 的 series 203 天 0 缺失、panel.js:2304→:2363 读的正是 series 行。教训：报「换数据源」前先把「数据到底缺不缺」用 3 行 python 直接打印确认（json 是 dict 日期键，别猜 rows/stats 键名）。
- 验证脚本断言用类名字符串判定「元素已删」会误命中 CSS 与注释 → 断言前先剔掉 `//` 注释行、或直接匹配 `<rect ... class="x">` 实体，别看纯类名。
- 从大文件正则抽出纯函数单独求值，若它调用了同文件其它模块级小工具（如 cycleNum）会 ReferenceError → 抽出时把依赖函数一起注入 new Function 作用域，别只抽目标函数。
- [面板数据更新] 不知「数据更新」该跑什么 / 找不到报告文件。解法：一条命令 `tools\panel_refresh.py --phase close`（内含 auto_task --mode close + cycle_collect）；报告是全历史合并的 `output\latest.md`（8.9MB），不是 `data\latest.md`；`/cycle.json`、`/latest.md` 由 8765 每请求重读磁盘，刷新后只需 F5，不重启服务。
- [2026-11-10] dsh-longtou：PowerShell `Get-Content output\refresh_manual.log` 看到中文全是乱码（如「瑙﹀彂」），只是显示问题 —— 项目所有日志都是 UTF-8，必须加 `-Encoding utf8` 再读，别误判成写入编码错。
- 2026-11-10 eltdx f10 `limit_board_ladder` 一次拉 448 个交易日 → 进程 exit 1、**无 Python traceback**（原生层被打断，日志只有 exit=1）；解法：按 40 个交易日分块请求再合并（`tools/build_ladder_cache.py --chunk 40`）。现象词：f10 梯队 拉整年 静默退出 / 命令 exit 1 无报错
- Windows 上 `Path.write_text`/`open("w")` 文本模式会把 `\n` 变成 `\r\n`，用 `read_text` 读回再比字节数会少算每行 1 字节，别据此判断"写入丢了内容"；要比就比 `Get-Item` 的 Length + CRLF 计数。
- 项目 package.json 带 "type": "module" 时，项目目录下随手写的临时 node 脚本（.js）会按 ESM 解析，`require is not defined` → 临时脚本一律用 .cjs 后缀（dsh-longtou、2026-11-10）
- 2026-11-10 读 DSH 子代理 session 记录（`E:\dsh-home\storages\session_projcache\sessions\<childId>.json`，约 300KB）判断是否还活着：别用 `ConvertFrom-Json`（这个大 JSON 直接抛 ArgumentException）也别 `Get-Content`（刷 14KB 进上下文）。用 python 只取两个字段：`record.rows.sessionStats.val.openStep`（非 null = 在跑）与 `record.rows.turnBoundary.val.lastStepBoundary.kind == "end"`（已收工）。


- 面板加控件时「放在X里面」= 字面放进 X 的容器/DOM 内部，不是「放在 X 附近」。2026-11-10 把年份翻页放到「交易日」行（`.lt-head-actions`，紧邻 `#dsh-longtou-date`），用户怒斥「放到选日期里面」。现象词：放到/放在/挪进 XX 里面、位置不对。解法：先定位目标控件的**容器 id 与展开逻辑**，把节点挂进该容器内部，点击委托也改绑到新父节点；验收断言「旧位置该节点数量为 0」。

- [截图验证] CDP 用 clip / 元素坐标截图产出空白 PNG（11KB、1.4KB 纯白），整屏 `Page.captureScreenshot`（不带 clip）才正常 → 截图后必须做非空白校验（zlib 解 IDAT，统计非 0xFF 字节占比 >2%），否则等于没有证据。
- [前端容器] 往「按子节点遍历分组」的容器里塞一个 header 兄弟节点，会导致分组错位（月标题串年）→ header 必须是分组容器的兄弟、平级挂载，且计数用「实际铺出的节点数」而非数据源 length。
- 点击图表某日 → 横向容器 scrollLeft 被重置到最右：DOM `insertBefore` 挪动节点时浏览器会**静默清零** scrollLeft（不走 JS setter），所以快照必须放在挪动**之前**，且没布局(scrollWidth<=0)时不读，否则把 0 当用户位置；容器被拆离后异步补的 scroll 事件也会用 0 冲掉记忆值，别用 scroll 监听记位置。
- F10 TQLEX `limit_board_ladder` 的 `market_overview[].trade_date` 是 `"2025-06-03"` 带横杠；用 `isdigit()` 过滤会整段静默判空（结果 days=0 却不报错）。取数字再校验长度 8。
- dsh-longtou 的日报家数（`/report.md`）是生成时刻的快照，改了 market_stats 必须重生成报告，否则面板数字不会动。
- 现象：CDP/无头浏览器里点面板内部的横向滚动容器里的元素，`element.click()` 或 `Input.dispatchMouseEvent` 都可能静默落空（坐标在视口外），数据看着正常其实没点上。解法：点前先校验 `getBoundingClientRect()` 在视口内 + `document.elementFromPoint()` 命中目标；内部容器用 `scrollIntoView` 带不进视口，得先设容器 `scrollLeft`。
- 现象：面板「选日期」跳到远处某天，连板天梯横向滚动条原地不动，用户找不到那一列（lib/panel.js 无 scrollIntoView，选日期回调只 renderCurrent）。注意：修的时候不能让"点可见列"也滚，否则会退回老 bug（甩最右）。
- dsh-longtou 的临时脚本/日志落在 **项目内** `dsh-longtou\.dsh-switchman\tmp\`，不是工作区根的 `.dsh-switchman\tmp\`；交接单里写错一级，新会话跑 `verify_backfill.py` 直接 No such file（2026-11-11 实际踩到，白跑一轮）。写交接单时用 `glob **/<脚本名>` 确认真实路径。
- [2026-11-11] [dsh-longtou 面板「常驻」假象] 现象：跑 CDP 探针前 8765 连接直接被拒，交接单却写「pythonw 常驻、禁止重启」。真相：`data/panel_restart.log` 末条是 11:22 重启失败（旧实例被 kill、新实例没起来，即已知的重启 bug），服务已死约 10 小时。定式：任何探针前先 `GET /health` + `Get-NetTCPConnection -LocalPort 8765`；无监听时用 `Invoke-CimMethod Win32_Process Create`（CommandLine=`pythonw tools\panel_server.py --port 8765`，CurrentDirectory=项目根）恢复单个实例，/health 报出 pid 且端口只剩一个持有者才算好；这不是「重启运行中的服务」，但要向用户明说。


- [2026-11-15] 做 Windows 桌面一键 .cmd 启动器时命令行里写中文导致 cmd.exe 按 ANSI 码页拆碎整行报 "The system cannot find the path specified" / "'le' is not recognized"：.cmd 内容保持纯 ASCII，中文提示全部交给被调用的 .ps1；且本机无 pwsh，ps1 要存成 UTF-8 **带 BOM** 才能在 PowerShell 5.1 里正常显示中文。
- [Windows 搜索索引重建] SearchIndexer 吃 2.7GB + Windows.edb 涨到 107GB（正常<2GB）：Stop-Service WSearch -Force → 重命名 Windows.edb → Start-Service 报 StartServiceFailed（7031 服务意外终止）但 SCM 30 秒后自动重启，新 edb 自动重建并自行清掉 .bak，C: 回收 107GB。别被 Start-Service 的报错骗了，等 30 秒再看 Get-Service。
- 2026-11-16 用 write/edit 改 dsh-longtou\tools\open_panel.ps1 加了中文注释后，PS 5.1 报 `Unexpected token ')'`——文件被存成无 BOM UTF-8，PS 5.1 按 ANSI 解析中文就撕语法。解法：`[IO.File]::WriteAllText($p,[IO.File]::ReadAllText($p),New-Object Text.UTF8Encoding($true))` 重存带 BOM，再 `[scriptblock]::Create($raw)` 验语法。

- [2026-11-16] dsh-longtou 面板梯队/候选停在旧日期（如还是 13 号）时，别查面板：先看 data/market_stats.json 的 mtime；panel_refresh.py 只有 open(09:25)/close(15:00) 阶段跑 auto_task 重建 market_stats，noon/pm 只跑 cycle_collect，而 cycle 的基准日取自 market_stats 最新交易日，所以 market_stats 停住 ⇒ 梯队/候选一起停住。解法：.venv\Scripts\python.exe tools\panel_refresh.py --catchup（会自动按 close 阶段重建并接着跑 cycle）。- 2026-11-17 dsh-longtou 面板梯队历史日冒出 ST 字样（如 0710 显示 ST龙元）：数据没错，是 longtou/cycle_source.py 用 stock_names.json 的**当前名称**回填历史日 ladder；修法是在 ladder 名称处按当日 data/st_hist.json band 判定——band==10（当日 10% 档、非 ST）就剥掉 ST/*ST 前缀，band==5 或缺档才保留。别去改 build_st_hist 的 band 判定（那条"5% 极值价否决 band=10"已验证是错的方向）。改完需重启 8765 面板进程才生效。
