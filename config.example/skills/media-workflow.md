---
name: media-workflow
description: GIF/视频抽帧审阅、SVG/图表高质量光栅化与工作区零垃圾文件规范
---

# 媒体解析与渲染作业规范

## 1. 目录与临时空间纪律
- 单次 `sandbox__exec` 内的抽帧、拼图、中间音频与临时转换文件一律写入 `/tmp`（命令结束随容器自动销毁，不污染 `/workspace`）。
- 调用 `qq__save_media_to_workspace` 下载群内媒体时省略 `destination`，让系统自动存入 `.scratch/`。
- 只有需跨多轮留存或最终发回群聊的成品，才放入清晰的主题子目录（如 `exports/`），严禁散落在 `/workspace` 根目录。

## 2. 动图（GIF / WebP）与视频快速审阅
- 先用 `ffprobe -v error -show_entries format=duration:stream=width,height,nb_frames -of json <file>` 查看时长与帧数。
- 抽取代表性关键帧并拼接为单张网格预览图到 `/tmp/grid.png`，减少多图读取开销：
  ```bash
  ffmpeg -y -i .scratch/input.gif -vf "fps=2,scale=320:-1,tile=4x3" -frames:v 1 /tmp/grid.png
  ```

## 3. SVG 与信息图高清渲染
- 沙箱已预装 `cairosvg`、`resvg-py`、`matplotlib` 与 Noto CJK/Emoji 字体：
  - 优先使用 `resvg-py` 或 `cairosvg.svg2png(url=..., write_to=..., scale=2.0)` 渲染高分屏清晰 PNG，避免手写脆弱的像素坐标估算。
  - 生成图像后用 `qq__send_message` 通过 `workspace_path` 发送前，可自检分辨率与留白是否正常。
