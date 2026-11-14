# sgf-review v2.1.0 · 液态玻璃界面 + Windows 运行修复

> 上一版在 Windows 实机上暴露的问题，这一版逐项修掉；桌面界面整体重做为苹果式**液态玻璃（Liquid Glass）**。

## 一、界面重做：液态玻璃

| 维度 | 做法 |
|---|---|
| 卡片材质 | 裁切背景 → 高斯模糊 → 提亮 → 白色微叠加 → 圆角遮罩 → 1px 高光描边 + 顶部内弧线，是真正的 backdrop blur，不是半透明色块 |
| 背景 | 深空垂直渐变 + 四团径向柔光（冰蓝 / 靛紫 / 暖金 / 青绿）+ 细噪点，避免大面积纯色发闷 |
| 阴影 | 每张卡片下方 13px 高斯模糊黑影，玻璃板从背景里浮起来 |
| 按钮 | 胶囊形，主按钮冰蓝→蓝紫渐变 + 白色描边，hover 提亮；次按钮半透明石墨底 |
| 勾选 | 17px 圆角方块，选中为青蓝渐变 + 白色对勾 |
| 滑块 | 4px 细轨道 + 双层圆形滑块，数值右对齐不压轨道 |
| 进度条 | 圆头粗线，底层石墨槽 + 青蓝渐变填充，随窗口自适应宽度 |
| 输入框 | 圆角凹槽 + 无边框 Entry，聚焦时描边变冰蓝 |
| 日志区 | 圆角深色凹槽，等宽字体 |
| 窗口 | Windows 11 自动启用深色标题栏（DWM 属性 20）与圆角窗口（属性 33）；启动 160ms 淡入 |
| 高分屏 | 声明 per-monitor v2 DPI 感知，按实际 DPI 设置 tk scaling，不再整窗发虚 |

## 二、Windows 运行问题修复

| 现象 | 根因 | 修复 |
|---|---|---|
| 双击后进程越开越多 / 卡死 | `ProcessPoolExecutor` 在 Windows 用 spawn，PyInstaller 冻结后子进程重跑入口，无限套娃 | `sgf_gui.py` / `sgf_review.py` 入口补 `multiprocessing.freeze_support()` |
| 渲染时反复闪黑框 | 每次启动 ffmpeg 都新建控制台 | `encoder.py` / `audio.py` 全部子进程加 `CREATE_NO_WINDOW` |
| 渲染反而比单进程更慢、内存翻倍 | onefile 每个 worker 都要解压一份完整程序 | Windows 下未显式指定 `-j` 时收敛为单进程；`--eco` 下 ffmpeg 限 2 线程 |
| 点「停止」后文件被占用、删不掉 | `terminate()` 只杀父进程，ffmpeg 成孤儿 | 改用 `taskkill /F /T /PID` 杀整棵进程树 |
| 高分屏界面模糊 | 未声明 DPI 感知，被系统位图拉伸 | `SetProcessDpiAwareness(2)` + 按 DPI 设置 tk scaling |
| 放了 BGM 没声音 | `audio.py` 硬编码 `"ffmpeg"`，不走同目录探测 | 统一改用 `encoder.ffmpeg_exe()`，绿色版同目录 ffmpeg 可被找到 |
| 小屏笔记本底部按钮看不见 | 窗口固定 980×880 | 按屏幕可用尺寸自适应，最小 900×700 |
| 中文日志乱码 / 解码报错 | cp936 管道 | 子进程环境强制 `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8`，读取端 `errors="replace"` |
| 构建机上图标生成失败 | Windows runner stdout 为 cp1252，中文 print 抛 `UnicodeEncodeError` | 图标脚本输出改纯 ASCII，workflow 全局 UTF-8 |

## 三、其它改进

- 版本号统一为 `2.1.0`：`--version`、JSON 元数据、界面右上角全部动态读取，不再有硬编码。
- 界面参数改动即时刷新右侧配置摘要。
- GUI 默认开启低占用模式，后台渲染时不影响前台办公。
- 预览、试运行、性能基准、棋谱信息共用同一条 `--cli` 子进程通道，随时可停止。

## 四、安装与使用

**Windows（推荐）**

1. 下载 `sgf-review-win64.zip`（约 70 MB）并解压到任意目录；
2. 双击 `sgf-review.exe` 打开界面（首次冷启动 2–4 秒，属单文件解压）；
3. 选择 SGF → 「预览画面」确认构图 → 「开始渲染」。

**源码运行**

```bash
pip install -r requirements.txt
python3 sgf_gui.py            # 图形界面
python3 sgf_review.py game.sgf -o out.mp4
```

## 五、已知限制

- 单文件 exe 每次启动都要解压到临时目录，冷启动 2–4 秒；这是 onefile 的固有代价。
- 单文件模式下多进程渲染收益为负，已默认关闭；需要并行请用源码运行并显式 `-j N`。
- 预览区与日志区不支持拖拽调整高度，窗口整体可缩放。
- 热力图需要棋谱内含 `ownership` 分析数据（LizzieYzy / KataGoTRT 导出），普通棋谱只渲染棋盘与 AI 面板。
