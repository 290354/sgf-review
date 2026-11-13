SGF Review v2.0.0 · Windows 绿色版
======================================

【快速开始】
1. 双击 sgf-review.exe 打开图形界面。
2. 点「浏览…」选择一个 .sgf 棋谱（examples 文件夹里有各种示例）。
3. 选方案（默认 heat = 领地热力图复盘），点「开始渲染」。
4. 成片保存在「输出视频」一栏指定的路径。
   建议先点「预览画面」看 2×2 分镜，再决定要不要改主题 / 时长。

【请保持文件完整】
本压缩包里的 ffmpeg.exe 必须与 sgf-review.exe 放在同一目录，程序会自动找到它。
若被误删，可以：
  · 重新下载 ffmpeg 并加入系统 PATH；
  · 或设置环境变量 SGFR_FFMPEG 指向任意 ffmpeg.exe 的完整路径。

【低占用模式（默认开启）】
最多 2 个渲染进程、限制 BLAS 线程数、关闭逐帧过渡动画。
后台渲染时可以正常浏览网页、办公，不会把 CPU 吃满。
想更快出片：在界面里取消勾选「低占用模式」，或调大「并行进程」。

【命令行用法】
这个 exe 同时保留完整命令行能力（参数与 Python 版一致）：
  sgf-review.exe --cli game.sgf -o out.mp4 --preset heat
  sgf-review.exe --cli --list-presets
  sgf-review.exe --cli game.sgf --info
  sgf-review.exe --cli game.sgf --dry-run
  sgf-review.exe --cli game.sgf --benchmark
在 CMD 或 PowerShell 中运行即可看到日志与进度条。

【常见问题】
Q: 双击后闪一下黑窗口？
A: 正常。命令行通道需要控制台来输出日志，界面启动后会立刻把它隐藏。

Q: 提示「该棋谱不含 ownership 领地数据」？
A: 领地热力图需要 LizzieYzy / KataGo 分析过的 SGF。普通棋谱请用
   review / shorts 方案，或取消勾选「领地热力图」。

Q: 视频没有声音 / 编码失败？
A: 确认 ffmpeg.exe 与 exe 同目录且可运行；也可在命令行里加 --no-audio
   先排除音频问题。

Q: 界面字体显示异常？
A: 程序自动使用 Windows 自带的微软雅黑 / 宋体，无需安装额外字体。

项目主页：https://github.com/290354/sgf-review
