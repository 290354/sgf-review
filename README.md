# SGF Review · 围棋对局回顾视频生成器

把任意 SGF 棋谱渲染成一条**电影感的对局回顾视频**：榧木棋盘、立体棋子、落子动画、提子效果、职业解说字幕、木质落子音效，全部由程序生成，不依赖任何素材包。

```bash
python3 sgf_review.py 棋谱.sgf -o 回顾.mp4
```

> 20 秒看懂效果：`output/01_alphago_vs_leesedol.mp4`（AlphaGo vs 李世石第四局，带职业解说）

---

## 1. 快速开始

```bash
# 依赖: Python 3.9+ / Pillow / NumPy / ffmpeg (含 libx264 或 libopenh264)
pip3 install pillow numpy

# 基础用法
python3 sgf_review.py game.sgf -o out.mp4

# 竖屏短视频 (1080x1920)
python3 sgf_review.py game.sgf --size vertical --duration 45 -o shorts.mp4

# 换个风格 + 控制时长 + 加水印
python3 sgf_review.py game.sgf -t midnight --duration 90 --watermark "@我的频道" -o out.mp4

# 片段导出 (剪辑素材): 第 80-120 手, 或 12.5-30.2 秒
python3 sgf_review.py game.sgf --range 80-120 -o clip.mp4
python3 sgf_review.py game.sgf --range 12.5-30.2 -o clip.mp4

# 一揽子附属导出 (不渲染主视频)
python3 sgf_review.py game.sgf --no-video \
  --srt out.srt --json out.json --poster poster.png \
  --theme-sheet themes.png --gif preview.gif --highlights key.mp4

# HEVC 编码 (同画质体积约 -30%)
python3 sgf_review.py game.sgf --codec hevc -o out.mp4

# 只出一张四宫格预览图, 先看效果再渲染
python3 sgf_review.py game.sgf --preview preview.png

# 看棋谱解析结果 (不渲染)
python3 sgf_review.py game.sgf --info

# 合集文件里指定第几局
python3 sgf_review.py collection.sgf --game 2 -o game2.mp4
```

## 2. 参数

| 参数 | 说明 |
|---|---|
| `-o, --output` | 输出路径；多个输入时可省略（自动命名） |
| `-t, --theme` | `classic` 榧木经典 / `midnight` 暗夜极光 / `paper` 素纸禅意 / `ink` 水墨留白 / `snow` 雪地青瓷 / `sakura` 绯樱夜宴 |
| `-s, --size` | `1080p` `720p` `1440p` `4k` `vertical`(1080×1920) `square`(1080×1080)，也可写 `1920x1080` |
| `--fps` | 帧率，默认 30 |
| `--speed` | 整体节奏倍率，越大越快 |
| `--duration` | 目标总时长（秒），自动给每一手分配停留时间 |
| `--labels` | 棋盘手数标注：`none` / `last` / `recent` / `all` |
| `--label-count` | `recent` 模式显示最近几手，默认 6 |
| `--no-comment` | 不显示棋谱注释 |
| `--no-coords` | 不显示棋盘坐标 |
| `--lang` | `zh` / `en` 界面语言 |
| `--watermark` | 右下角水印 |
| `--bgm` | 背景音乐（mp3/wav/m4a），会与落子音效自动混音 |
| `--bgm-gain` | BGM 音量 0~1，默认 0.20 |
| `--no-audio` | 关闭落子音效 |
| `--no-analysis` | 关闭 AI 分析面板（即使棋谱带 LizzieYzy 数据） |
| `--heatmap` | 在棋盘上叠加 AI 领地热力图（需 ownership 数据） |
| `--no-ai-commentary` | 关闭根据胜率变化自动生成的点评字幕 |
| `--line` | 有分支时取哪条主线：`longest` / `first` / `last` |
| `--quality` | `low` / `medium` / `high` / `ultra` |
| `--codec` | `h264` / `hevc` / `vp9` |
| `--range` | 只渲染片段：`80-120`（手数）或 `12.5-30.2`（秒） |
| `--gif` | 额外导出循环 GIF（`--gif-fps` `--gif-scale` 可调） |
| `--srt` | 导出 SRT 字幕（可导入剪映/PR），人类评论优先，其次 AI 点评 |
| `--json` | 导出 JSON 元数据（手数/分析/时间轴/关键手），供二次创作 |
| `--poster` | 导出海报帧 PNG（`--poster-move N` 指定手数） |
| `--theme-sheet` | 导出 6 主题对比图 |
| `--highlights` | 另出关键手集锦短片（AI 失误/好手，顶部横幅标注） |
| `--no-video` | 不渲染主视频，只做上述附属导出 |
| `--intro` / `--outro` | 片头/片尾时长（秒） |
| `--no-intro` / `--no-outro` | 去掉片头/片尾 |
| `-j, --jobs` | 并行进程数，默认按容器 CPU 配额与内存自动决定 |
| `--preview` | 输出 2×2 预览拼图 |
| `--info` | 只打印解析结果 |
| `--list-themes` | 列出主题 |

## 3. SGF 兼容性（实测）

用 **5637 局真实棋谱**做过全量回归，解析 + 合法性校验 **100% 通过**：

| 来源 | 数量 | 说明 |
|---|---|---|
| KGS 高段对局 (2019-04) | 1255 | 含 471 局让 2~7 子，全部带服务器标签、超时/弃权结果 |
| AlphaGo 全部对局 | 221 | 含李世石五番棋、60 局自我对局，**带樊麾职业解说评注** |
| 赵治勋 | 2800 | 日本头衔战棋谱 |
| 吴清源 | 891 | 含 1930–60 年代对局 |
| 本因坊秀策 | 470 | 19 世纪古谱 |

已处理的"各种情况"：

- **编码**：UTF-8 / GBK·GB2312 / Big5 / Shift-JIS / EUC-KR / UTF-16（带 BOM）；`CA[]` 声明会优先采用，声明错误时靠结构+常用字打分纠正
- **棋盘尺寸**：9 / 13 / 19 路，也支持 `SZ[19:13]` 非方形棋盘
- **让子与摆子**：`HA[]` + `AB[]`/`AW[]`，让 2~9 子都能正确显示在星位
- **提子**：整块提子、多块同时提、自杀手会被判定为非法并跳过（不中断渲染）
- **虚手**：`B[]` / `W[]` 会显示"虚手"并给更长停留
- **注释**：`C[]` 多段文本自动换行、按空间自动缩放、超长自动省略
- **分支**：`(;B[..](;W[..])(;W[..]))` 变体树，按 `--line` 取主线（默认最长）
- **合集**：一个文件多局 `(;...)(;...)`，`--game N` 选择
- **结果写法**：`B+R` `W+Resign` `B+Time` `B+Forfeit` `W+9.5` `B+0.5` `0` `?` `黑中盘胜` 等
- **标记**：`LB` 字母标记、`TR`/`SQ`/`CR`/`MA` 形状标记、`TB`/`TW` 领地
- **零手对局**：只剩摆子也会正常出片
- **补全**：缺 `PB`/`PW`/`RE`/`KM` 时用"黑方/白方"等占位，不会崩
- **AI 分析**：LizzieYzy / Lizzie 的 `LZ` `LZ2` `LZOP` `LZOP2` 扩展属性，含部分分析、老版本 0.7.x、空标签、超长 bestmoves

## 4. AI 分析（LizzieYzy / Lizzie）

棋谱里带 `LZ[...]` 属性时自动进入**分析模式**：面板多出胜率曲线、目差曲线与 AI 信息卡，棋盘上标出 AI 推荐点。不需要额外开关，`--info` 会打印识别结果。

```bash
python3 sgf_review.py 分析过的棋谱.sgf -o 复盘.mp4 --duration 150
python3 sgf_review.py 分析过的棋谱.sgf --heatmap      # 叠加领地热力图
```

### 4.1 解析的字段

LizzieYzy 在每个分析过的节点写入：

```
LZ[KataGoTRT 44.6 385 0.2 17.6
move F2 visits 184 winrate 5540 prior 3979 scoreMean 0.20 pv F2 N5 O5 ...
info move B3 visits 43 winrate 4888 prior 4103 scoreMean -0.50 pv B3 B4 ...
ownership -0.03 0.11 ...]
```

| 字段 | 含义 |
|---|---|
| `engine winrate playouts scoreMean scoreStdev pda` | 首行：引擎名、胜率、计算量（支持 `54k`/`6.4k`/`1.2m`）、目差、标准差、PDA |
| `move / visits / winrate / prior / scoreMean / pv` | 候选点列表；winrate 以 1/100 百分点存储（`5540` = 55.40%） |
| `ownership` | 361 个交叉点的归属（-1 白 ~ +1 黑），行优先 |
| `LZ2` | 副引擎，同样解析，可画双引擎对比曲线 |
| `LZOP` / `LZOP2` | 开局分析，读取但不参与主线 |

`C[]` 里 LizzieYzy 自动追加的「黑棋 胜率: 52.0% / 盘面: -6.8 / (KataGoTRT / 82 计算量)」会被识别并剥离，**只保留人类写的解说**作为字幕；首行的绝艺胜率标签（`/"jueyi黑65.3%`）同样归入引擎块。没有解说时，用胜率变化自动生成点评（`--no-ai-commentary` 可关）。

### 4.2 视角归一化（容易踩的坑）

Lizzie 的字段视角并不统一，直接画会得到反相的曲线：

- 首行 `winrate` 是**刚落子一方**的胜率
- `scoreMean` 是**轮到的一方**的目差
- 候选点的 `winrate` 又是**轮到的一方**

程序统一换算成「黑方胜率 / 黑方目差」再绘制：`black_wr = wr`（黑落子）或 `100 - wr`（白落子）；`black_lead = ±scoreMean`（视轮谁走）。这样曲线、卡片、点评的符号永远一致。

### 4.3 画面上的分析元素

- **胜率曲线**：黑方视角 0–100，50% 基线，未走到的部分以 13% 透明度预铺（能看到整局走势），当前手实色 + 游标；单步波动 ≥9% 打点（红=跌，绿=涨）
- **目差曲线**：黑方视角，范围自适应，0 线居中
- **计算量**：胜率图底部的淡色面积（对数刻度），一眼看出哪几手是深算
- **关键手**：曲线上的红点对应「失误 / 严重失误」，片尾汇总最大失误
- **AI 卡片**：当前手胜率、目差、visits、引擎名 + 下一手推荐点（前 2）
- **棋盘推荐点**：AI 对下一手的首选，青色圆环 + 中心点
- **领地热力图**（`--heatmap`）：ownership 转成空点上的半透明色块（蓝=黑，米=白）
- **AI 复盘**：没有解说的手，底部显示吻合度、已分析手数、平均损失、最大失误、引擎与平均 visits；统计只算已经下过的部分，最大失误标注执子方（如「第 218 手（白）」），不会提前剧透后面的手
- **片头 / 片尾**：标注「AI 分析 · 引擎 · 手数」与「AI 吻合度 / 最大失误」

部分分析（例如只分析了中后盘）不会中断渲染：曲线从有数据的那手开始，缺口 ≤8 手线性补间，卡片显示「本手无分析数据」。

### 4.4 实测样本

| 棋谱 | 手数 | 分析覆盖 | 引擎 | 说明 |
|---|---|---|---|---|
| 第29届LG杯决赛第1局 柯洁 vs 卞相壹 | 288 | 282 (98%) | ikatago | 2000k 计算量，最大失误 第218手（白）-78.3% |
| 第27届LG杯半决赛 丁浩 vs 姜东润 | 121 | 89 (74%) | KataGoTRT | 含 ownership，最大失误 -32.9% |
| 第14届春兰杯半决赛 申真谞 vs 李轩豪 | 170 | 38 (22%) | KataGoTRT | 关键片段分析 |
| 第28届LG杯 柯洁 vs 柁嘉熹 | 263 | 16 (6%) | KataGoTRT | 仅分析若干关键手 |
| yamanetoshi 2021-02-26 | 54 | 54 (100%) | Lizzie 0.7.4 | 老版本格式，无 ownership |

## 5. 画面构成

**棋盘**
- 程序化榧木纹理（多尺度 fbm 弯曲 + 年轮暗线 + 纵向包络），每局生成一次并缓存
- 星位、坐标、细边框、外沿压暗、内沿微亮

**棋子**
- 逐像素球面光照：主光 + 半球环境光 + Blinn-Phong 高光 + 菲涅尔边缘光 + 棋盘反光
- 高光强度随棋子半径自适应（19 路小棋子不会退化成"一个白点"）
- 带方向性投影，阴影随动画缩放

**动效**
- 落子：从上方落下 + 缩放 + 透明度 + 阴影收拢（0.1~0.34s，随节奏自适应）
- 提子：被提棋子淡出
- 最后一手：金色呼吸圆环
- 片头 / 片尾：元素逐个上浮淡入，与正片交叉溶解

**信息面板**（横屏 / 竖屏两套布局）
- 赛事 · 日期 · 双方姓名与段位
- 手数大字号 + 当前落子方 + 双方提子数
- 最近手数双栏列表（带小棋子与坐标）
- 有解说时显示解说正文；无解说时显示贴目/规则/地点/用时
- 底部进度条

**声音**
- 落子音：木质敲击（噪声激励 + 阻尼正弦 + 短混响），每手触发一次，力度随机
- 支持外挂 BGM，自动混音并做响度归一

## 6. 性能

在 4 核 / 8GB 的容器里：

| 场景 | 帧数 | 耗时 |
|---|---|---|
| 180 手 19 路 · 1080p30 · 2:09 | 3868 | ≈ 40 s |
| 80 手 9 路 · 1080p30 · 1:00 | 1800 | ≈ 22 s |
| 45 秒竖屏 · 1080×1920 | 1350 | ≈ 16 s |

并行策略：读取 cgroup 的 CPU 配额与内存上限自动定进程数；若进程被系统回收会**自动降级重试**（8 → 4 → 2 → 1），不会整条任务失败。

## 7. 目录结构

```
sgf-review/
├── sgf_review.py          # 命令行入口
├── sgfr/
│   ├── sgf.py             # SGF 词法/语法解析、编码识别、变体树
│   ├── board.py           # 棋盘规则：落子、提子、合法性
│   ├── timeline.py        # 节奏编排：按注释长度/提子/虚手分配时长
│   ├── theme.py           # 4 套主题（棋盘/棋子/背景/文字）
│   ├── graphics.py        # 木纹、棋子球面光照、背景、发光
│   ├── boardview.py       # 棋盘纹理与棋子层合成
│   ├── lz.py              # LizzieYzy 分析解析、视角归一、关键点/吻合度
│   ├── charts.py          # 胜率/目差曲线图层（预渲染 + 裁剪）
│   ├── renderer.py        # 场景、动画、面板、片头片尾
│   ├── audio.py           # 落子音效合成与混音
│   ├── encoder.py         # ffmpeg 管道、分段、拼接、混流
│   └── cli.py             # 参数与主流程
├── examples/              # 各类形态的示例棋谱（让子/变体/合集/GBK/13路…）
├── records/               # 真实棋谱库（5637 局）
│   ├── kgs/2019-04/       # KGS 7d+ 对局 1255 局
│   └── pro/               # alphago / cho_chikun / go_seigen / shusaku
├── tools/fetch_records.py # 棋谱下载脚本
└── output/                # 演示视频
```

## 8. 棋谱来源与版权

`records/` 里的棋谱来自两个公开归档，仅用于测试与演示：

- KGS 高段对局：<https://u-go.net/gamerecords/>（KGS 服务器公开对局，已剔除注释与变化图）
- 日本职业棋谱：<https://homepages.cwi.nl/~aeb/go/games/>（Andries Brouwer 整理）

重新下载：

```bash
python3 tools/fetch_records.py kgs 2019_04
python3 tools/fetch_records.py pro alphago
```

对局棋谱本身通常不构成作品，但**解说文字有版权**（如 AlphaGo 对局的樊麾评注），用于公开发布前请自行确认授权。

## 9. 演示视频

| 文件 | 内容 |
|---|---|
| `output/01_alphago_vs_leesedol.mp4` | AlphaGo vs 李世石 第四局（樊麾解说），19 路 · 2:09 |
| `output/02_9x9_goseigen.mp4` | 吴清源 9 路对局，水墨主题 · 1:00 |
| `output/03_handicap6.mp4` | KGS 让 6 子对局，深夜主题 · 1:20 |
| `output/04_vertical_short.mp4` | 竖屏 1080×1920 短视频 · 0:45 |
| `output/05_lizzieyzy_analysis.mp4` | 丁浩 vs 姜东润（LizzieYzy 分析），胜率/目差曲线 · 2:30 |
| `output/06_analysis_vertical.mp4` | 同一局竖屏版，曲线并排 · 1:00 |
| `output/08_kejie_lg_final.mp4` | 第29届LG杯决赛第1局 柯洁 vs 卞相壹，98% 覆盖 · 2:15 |
| `output/09_kejie_lg_vertical.mp4` | 同一局竖屏版 · 1:00 |
