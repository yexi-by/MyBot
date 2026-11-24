# IndexTTS 2.5 部署与 MyBot 接入

本目录的 [`server.py`](server.py) 运行在配备 GPU 与模型权重的机器上，MyBot 通过 HTTP 调用语音合成与音色管理接口；模型与音色文件不打包进 MyBot 应用镜像。

## 准备推理环境

1. 按 [IndexTTS 官方说明](https://github.com/index-tts/index-tts)克隆仓库、安装依赖并下载 **IndexTTS-2.5** 权重：

   ```powershell
   git clone https://github.com/index-tts/index-tts.git $HOME\IndexTTS
   uv sync --project $HOME\IndexTTS --locked --extra webui
   uv tool run --from huggingface-hub hf download IndexTeam/IndexTTS-2.5 --local-dir $HOME\IndexTTS\checkpoints
   ```

   上游使用 Python 3.10/3.11 与 CUDA PyTorch，与 MyBot 的 Python 3.14 环境独立。`webui` extra 提供 HTTP 服务所需的 FastAPI 与 Uvicorn。

2. 在 **MyBot 仓库根目录**启动推理服务：

   ```powershell
   $env:INDEXTTS_ROOT = "$HOME\IndexTTS"
   $env:INDEXTTS_API_KEY = "自行生成的连接密钥"
   .\deploy\indextts\start_indextts.bat
   ```

   默认监听 `0.0.0.0:7861`，首次合成时按需加载模型（可加 `--preload` 启动预加载；支持 BF16 的显卡可加 `--bf16`，默认 FP32）。

3. `INDEXTTS_VOICES_DIR` 指定音色目录（默认 `deploy/indextts/voices/`）。可直接放入合法 ID 命名的标准 PCM WAV，或通过 MyBot 控制台上传（上传浮点 WAV 或 MP3/FLAC/OGG 需系统安装 `ffmpeg`，单文件上限 16 MB）。

## 在 MyBot 中管理与使用

在 WebUI「工具 → 语音合成」填写可达的服务地址、Bearer 密钥和主音色 ID：

- **模型面板**：查看在线状态、计算设备与显存占用，支持远程加载或释放显存。
- **音色库**：上传、试听、切换与删除参考音频（建议 3～15 秒清晰干声；当前使用中的主音色禁止删除）。
- **合成与情绪模式**：`tts__synthesize` 支持 `language`（默认 `zh`，可选 `en`、`ja`、`es`、`ar`）及四种情绪控制——`reference`（跟随主音色默认语气）、`text`（通过 `emotion_text` 文本描述驱动）、`vector`（八维情绪配比）与 `audio`（通过 `emotion_voice` 借用另一参考音频情绪），配合 `emotion_alpha` 调节强度。
