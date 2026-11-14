# sgf-review v3.0.0 · 最终版

> **项目状态：已完成，进入稳定维护。** 3.0.0 是功能与体验的收口版本——渲染、界面、Windows 打包、CI 与文档全部就绪并验证通过，后续只做缺陷修复，不再新增功能。

## 一、本版重点

| 改动 | 说明 |
|---|---|
| **修复 `--version` 崩溃** | v2.x 的 argparse 注册了两个 `--version`，任何带该参数的调用都会抛 `conflicting option string`。GUI 通过子进程调用 CLI，这正是 Windows 版「一运行就出问题」的根因。 |
| **关键手标记真正生效** | 胜率图新增关键手竖虚线（自动取影响最大的三手）与底轴三角标记；`key_marks` 此前只传入未绘制。 |
| **图表手数刻度** | X 轴补 `1 / 31 / 61 / 91 / 121` 手数标注，纵轴留白同步放宽，不再挤压曲线。 |
| **提子浮字** | 提子瞬间在提子质心上方浮出 `+n` 并淡出。 |
| **四套落子音效** | `--kit {kaya,slate,paper,ice}`：榧木 / 石板 / 纸面 / 清脆，提子音同步变化。 |
| **进阶设置面板** | GUI 新增液态玻璃「进阶…」对话框：背景音乐与音量、播放速度、合集局号、关键手集锦、GIF 帧率/缩放、海报手数。 |
| **偏好记忆** | 关闭窗口自动写入 `~/.sgf-review/gui.json`，下次启动恢复棋谱、参数与开关。 |
| **低占用不再被预设覆盖** | 选 `heat` / `review` 预设时不再把「低占用模式」重置为关闭，只有预设显式声明才改动。 |
| **配置键补全** | `--kit` 进入 `CONFIG_KEYS`，`--save-config` / `--config` 可持久化音效包。 |

## 二、工程收口

- `tools/selftest.py`：30 项回归，覆盖 SGF 解析、让子补全、压缩坐标、提子写回、LZ 视角归一、argparse 选项唯一性、预设合并、章节导出。
- `.github/workflows/selftest.yml`：Linux runner 跑回归 + `--version` / `--info` / 预设列表 + 6 秒 720p 渲染冒烟。
- 补 MIT `LICENSE`。
- 版本号统一 `3.0.0`，`--version`、JSON 元数据、界面右上角均动态读取。

## 三、v2 系列既有能力

- **领地热力图**：连续领地场（双线性采样 + smoothstep 边缘羽化），主题自适应冷暖色，首次淡入 + 数据切换 crossfade，作为棋盘底纹插在木纹与棋子之间；低分辨率栅格化 + 缓存。
- **实用工作流**：5 套预设、JSON 配置、`--dry-run`、`--benchmark`、章节导出、`--eco` 低占用、`--skip-existing` 断点续跑。
- **液态玻璃界面**：真实 backdrop blur 毛玻璃卡片、22px 圆角、外阴影浮层、全自绘按钮/勾选/滑块/进度条、深空渐变 + 四团柔光 + 细噪点、Windows 11 深色标题栏与圆角窗口。
- **Windows 稳定性**：`freeze_support`、子进程 `CREATE_NO_WINDOW`、默认单进程、`taskkill /T` 杀进程树、per-monitor DPI 感知、`ffmpeg_exe()` 统一探测、窗口按屏幕自适应。

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

- 单文件 exe 每次启动需解压到临时目录，冷启动 2–4 秒，属 onefile 固有代价。
- 单文件模式下多进程收益为负，已默认关闭；需要并行请用源码运行并显式 `-j N`。
- 预览区与日志区不支持拖拽调整高度，窗口整体可缩放。
- 热力图需要棋谱内含 `ownership` 数据（LizzieYzy / KataGoTRT 导出）；普通棋谱只渲染棋盘与 AI 面板。
- 关键手集锦需要棋谱内含分析数据，无分析时输出 0 手并跳过。

## 六、验证记录

| 项 | 结果 |
|---|---|
| `tools/selftest.py` | 30 / 30 通过 |
| 297 组逐帧压测 | 297 / 297 通过 |
| 端到端导出（视频 + BGM + GIF + SRT + JSON + 海报 + 章节） | 通过；BGM 440 Hz 频点确认混入 |
| GUI 启动 / 预览 / 进阶面板 / 偏好恢复 | 通过 |
| Windows 云端构建 | 12 步全 success，冒烟自检输出 `sgf-review 3.0.0` |
