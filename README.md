# WinKeyGuard

> 打游戏时自动禁用 Windows 键，退出游戏自动恢复。

打游戏时手一抖碰到 Win 键，开始菜单弹出来、游戏被切出去、画面卡住——回来的时候已经被坑了。这个小工具在游戏运行时吞掉 Win 键，游戏一退出立刻还给系统。

**不改注册表，不重写 Scancode Map，不需要重启，也不会留下"Win 键永久失效"的烂摊子。**

---

## 为什么不用改注册表那种方案

网上常见做法是改注册表的 `Scancode Map`，把 Win 键彻底映射掉。问题是：

- 改完要**重启**才生效
- 玩完得**手动改回来**，忘了就一直废着
- 正好和"游戏结束要恢复"的诉求相反

本项目改用**全局低级键盘钩子**（`WH_KEYBOARD_LL`）：只在游戏运行的那段时间安装钩子，把 Win 键的按下/抬起事件在系统看到之前吞掉。钩子属于进程，**进程结束系统自动回收**——正常关闭、Ctrl+C、任务管理器结束、甚至崩溃，Win 键都会立刻恢复。

---

## 特性

- **自动判断**：游戏进程出现 → 禁用；进程消失 → 恢复，无需手动开关
- **零残留**：钩子随进程生灭，不会把系统留在"Win 键坏了"的状态
- **两套判据**：进程名单（内置 49 个常见游戏）+ 全屏独占检测（名单外的新游戏也能自动生效）
- **误判防护**：全屏检测排除"最大化窗口"、浏览器、播放器，看全屏视频不会被误禁
- **无第三方依赖**：单文件 Python，只用标准库
- **可热加载**：改完 `config.json` 的名单不用重启
- **单实例**：重复启动会提示而不是起多个进程

---

## 环境要求

- Windows 10 / 11
- Python 3.8+（在 3.12 上实测）

无需 `pip install` 任何东西。

---

## 快速开始

下载或克隆本仓库后，进入项目目录，确认目录里有 `wingard.py` 和 `config.json`。

**1. 自检**

```bash
python wingard.py --diagnose
```

确认 `键盘钩子: 可以安装`、`Win 键状态: 正常`。

**2. 验证拦截真的有效**

```bash
python wingard.py --test-hook 10
```

这 10 秒内按 Win 键不会有任何反应，10 秒后自动恢复。这是最直观的验证。

**3. 启动守护**

```bash
python wingard.py          # 前台运行，看得到日志，Ctrl+C 退出
```

或者双击 `启动守护.bat` 后台运行（`pythonw` 启动，不留黑窗口）。

**4. 开游戏**

日志里会出现：

```
已禁用 Win 键 —— 游戏运行中：cs2.exe
```

游戏退出后 1 秒内：

```
已恢复 Win 键 —— 未检测到游戏
```

---

## 命令行

| 命令 | 作用 |
|---|---|
| `python wingard.py` | 前台运行守护 |
| `python wingard.py --status` | 查看当前状态（是否禁用、原因、PID） |
| `python wingard.py --stop` | 停止后台实例（先请求优雅退出，3 秒不退才强杀） |
| `python wingard.py --test-hook 10` | 临时拦截 N 秒用于验证，到点自动恢复 |
| `python wingard.py --add` | 把**当前前台程序**加入游戏名单 |
| `python wingard.py --add eldenring` | 按名字加入名单 |
| `python wingard.py --remove eldenring` | 从名单移除 |
| `python wingard.py --once` | 只检测一次并打印判定结果 |
| `python wingard.py --diagnose` | 环境自检 |

---

## 热键

守护运行期间生效：

| 热键 | 作用 |
|---|---|
| `Ctrl+Alt+L` | 手动锁定 / 解锁 Win 键 |
| `Ctrl+Alt+P` | 暂停 / 继续守护（暂停后 Win 键立刻恢复） |
| `Ctrl+Alt+Q` | 退出程序并恢复 Win 键 |

改 `config.json` 里的 `hotkeys` 可自定义，但**需重启生效**。热键被占用时日志会写一行 `注册失败`。

---

## 它是怎么判断"在打游戏"的

**判据一 · 进程名单（主力）**

`config.json` 的 `games` 列表，只要列表里的进程在运行就禁用。内置 49 个常见游戏（CS2、无畏契约、LOL、DOTA2、黑神话、原神、帕鲁、PUBG 等）。

进程名不带 `.exe` 也行，不区分大小写，支持 `*` 通配（如 `blackmyth*`）。

**判据二 · 全屏独占检测（补充）**

当前前台窗口独占整块显示器（面积占比 ≥ 98.5%）时也算游戏。这样没写进名单的新游戏同样生效。

特意排除的情况：
- **最大化的普通窗口**（最大化会避开任务栏，不满足占比；且显式检查 `WS_MAXIMIZE`）
- **浏览器 / 播放器等**，走 `fullscreen_exclusions` 名单

**新游戏没被识别？** 开着游戏跑 `python wingard.py --add`，会把当前前台进程加进名单。守护进程下一秒自动读到，不用重启。

---

## 配置

首次运行自动生成 `config.json`。改完不需要重启（热键除外）。

| 字段 | 默认 | 说明 |
|---|---|---|
| `games` | 49 个常见游戏 | 游戏进程名列表 |
| `detect_fullscreen` | `true` | 是否启用全屏检测 |
| `fullscreen_exclusions` | 浏览器/播放器 | 全屏检测排除名单 |
| `require_foreground` | `false` | `true` 时只有游戏在前台才禁用 |
| `poll_interval` | `1.0` | 检测间隔（秒），范围 0.2~10 |
| `beep` | `true` | 禁用/恢复时提示音 |
| `block_apps_key` | `true` | 顺带拦截菜单键 |
| `swallow_combo_keys` | `false` | 见下 |
| `pause_minutes` | `0` | 暂停多久后自动恢复，0 = 一直暂停 |

### `swallow_combo_keys` 说明

Win 键被吞掉后，`Win+D` 里的 `D` 仍会送到游戏。绝大多数游戏对它没反应，所以默认关闭。若你的游戏恰好用 `D` 做移动，可打开：Win 被按住期间的其他按键也一起吞掉。

带 2 秒超时保护——即使 Win 键抬起事件丢失，也不会出现"按什么都没反应"。

---

## 安全与反作弊说明

**这个工具做了什么：**
- 使用 `SetWindowsHookEx(WH_KEYBOARD_LL)` 安装键盘钩子
- 用 `CreateToolhelp32Snapshot` 枚举进程名
- 用 `GetForegroundWindow` / `GetWindowRect` 判断前台窗口是否全屏

**它没有做什么：**
- 不读写任何其他进程的内存
- 不注入 DLL
- 不模拟键鼠输入
- 不访问网络，没有任何上传/下载行为
- 不写注册表的系统敏感项

**关于反作弊**：虽然上述行为都是公开的标准 Windows API，但各家反作弊策略不同，**我无法替任何厂商保证不会被误判**。如果你玩的竞技游戏反作弊较严格，建议先用 `--test-hook` 试一次并观察，或仅在单机游戏时开启。自行评估风险。

---

## 常见问题

**Q：游戏以管理员身份运行，还有效吗？**

需要本工具也用管理员权限运行。否则钩子优先级不够，拦不住提权进程的按键。

**Q：程序被强制杀掉，Win 键会永久锁死吗？**

不会。钩子属于进程，进程结束系统自动回收。这是本方案最省心的地方。

**Q：双击 `.bat` 弹出"打开文件 - 安全警告 / 无法验证发布者"怎么办？**

这是 Windows 的 Mark-of-the-Web（网络标记）：从 GitHub 下载 ZIP 解压后，文件会带上 `Zone.Identifier` 标记，每次运行都弹框。

双击 `解除文件锁定.bat` 清除，或右键文件 → 属性 → 勾选「解除锁定」。命令行：

```powershell
Get-ChildItem . -Recurse -File | Unblock-File
```

**为什么不能签名解决？** `.bat` 在格式上无法被 Authenticode 签名（纯文本没有存放签名的 PE 结构），且该弹窗的判定依据是网络标记而非签名。

**Q：为什么 `.bat` 里全是英文？**

刻意的。cmd.exe 按系统代码页（中文 Windows 为 936/GBK）解析 `.bat`，文件若存成 UTF-8 + LF 换行，中文字节会被拦腰切断，整行变成乱码命令报错。所以脚本一律只写 ASCII，中文提示交给 `wingard.py` 打印。

**Q：日志在哪里？**

`logs/wingard.log`，自动轮转（最大 2MB × 3）。当前状态快照在 `logs/state.json`。

**Q：怎么开机自启？**

把 `启动守护.bat` 的快捷方式放进 `shell:startup`（Win+R 输入即可打开启动文件夹）。

---

## 文件说明

| 文件 | 作用 |
|---|---|
| `wingard.py` | 主程序，单文件，仅标准库 |
| `config.json` | 配置，首次运行自动生成 |
| `启动守护.bat` | 后台启动守护 |
| `停止守护.bat` | 优雅停止 |
| `自检.bat` | 环境 + 钩子 + 前台窗口自检 |
| `测试拦截.bat` | 10 秒拦截验证 |
| `解除文件锁定.bat` | 清除网络标记，消除安全警告弹窗 |
| `logs/` | 运行日志与状态快照 |

---

## 实现摘要

| 组件 | 做法 |
|---|---|
| 拦截 | `SetWindowsHookEx(WH_KEYBOARD_LL)`，在独立线程的消息循环里运行，需要拦截时才安装 |
| 检测 | `CreateToolhelp32Snapshot` 枚举进程 + `GetForegroundWindow` 全屏判定，默认 1 秒轮询 |
| 恢复 | `UnhookWindowsHookEx` + 线程 `PostThreadMessage(WM_QUIT)`；进程死亡时系统兜底回收 |
| 状态 | JSON 状态文件原子写入（临时文件 + `os.replace`） |
| 停止 | 写 stop 标志 → 守护自行优雅退出 → 3 秒未退才 `TerminateProcess` |

---

## License

<!-- 建议补上。没有 LICENSE 时默认保留所有权利，他人不能合法使用/修改/分发。 -->
MIT

---

## English

**WinKeyGuard** auto-disables the Windows key while a game is running and restores it the moment the game exits.

Implemented with a global low-level keyboard hook (`WH_KEYBOARD_LL`) — no registry edits, no Scancode Map rewrites, and no risk of leaving the Win key permanently disabled: the hook is owned by the process and is reclaimed by Windows when the process ends.

- Windows 10/11, Python 3.8+ (tested on 3.12), standard library only
- Ships with 49 common game process names plus fullscreen-exclusive auto-detection (excludes maximized windows, browsers and media players to avoid false positives)
- Hotkeys: `Ctrl+Alt+L` toggle lock, `Ctrl+Alt+P` pause, `Ctrl+Alt+Q` quit
- Quick check: `python wingard.py --test-hook 10` — press Win during those 10 seconds, nothing should happen
