# IndexTTS 2.5 部署与 MyBot 接入

本目录的 [`server.py`](server.py) 在装有 GPU 和模型的电脑上运行，MyBot 通过 HTTP 调用。MyBot 后端与 WebUI 仍由仓库根目录的完整 Docker 镜像交付；模型与音色不打包进应用镜像。

## 准备推理环境

1. 按 [IndexTTS 官方说明](https://github.com/index-tts/index-tts)克隆仓库，安装其锁定依赖并下载 **IndexTTS-2.5** 权重。以下命令在 PowerShell 中执行：

   ```powershell
   git clone https://github.com/index-tts/index-tts.git D:\work\IndexTTS
   uv sync --project D:\work\IndexTTS --locked --extra webui
   uv tool run --from huggingface-hub hf download IndexTeam/IndexTTS-2.5 --local-dir D:\work\IndexTTS\checkpoints
   ```

   上游使用 Python 3.10/3.11 和 CUDA PyTorch，与 MyBot 的 Python 3.14 环境分开。`webui` extra 提供 HTTP 服务需要的 FastAPI 与 Uvicorn。下载权重和首次加载辅助模型需要网络及足够磁盘空间。

2. 返回 **MyBot 仓库根目录**，启动服务：

   ```powershell
   $env:INDEXTTS_ROOT = "D:\work\IndexTTS"
   $env:INDEXTTS_API_KEY = "自行生成的连接密钥"
   .\deploy\indextts\start_indextts.bat
   ```

   启动脚本切换到 IndexTTS 仓库并使用它的 uv 环境，不会将模型依赖装进 MyBot。默认监听 `0.0.0.0:7861`，首次合成按需加载模型；可用 `--preload` 在启动时加载。默认 FP32；支持 BF16 的显卡可添加 `--bf16`，RTX 20 系列保持默认。四种情绪模式均可用，文本情绪所需的 QwenEmotion 会随模型加载。

3. 用 `INDEXTTS_VOICES_DIR` 指定音色目录，默认 `deploy/indextts/voices/`，已忽略提交和镜像构建。可放入合法 ID 命名的 PCM WAV，或通过 MyBot 上传。上传浮点 WAV（包括官方示例）及 MP3/FLAC/OGG 需要 ffmpeg；标准 PCM WAV 无需转换。音频及转换后的 WAV 均不得超过 16 MB。

## 在 MyBot 中管理

在「工具 → 语音合成」填写可从 MyBot 主机访问的服务地址、连接密钥和主音色 ID。远端 MyBot 中的 `127.0.0.1` 指向远端主机；模型在另一台电脑上时应填写那台电脑的可达地址。局域网调用通常不使用代理。

- 模型面板查看在线状态、设备和显存；可加载或卸载模型。卸载后下一次合成会重新按需加载。
- 音色库可上传、试听、切换和删除参考音频；正在使用的主音色不能删除。建议上传 3～15 秒清晰干声。
- 试听可选择台词语言及四种情绪模式。Agent 的 `tts__synthesize` 同样支持 `language`，默认 `zh`，其他值为 `en`、`ja`、`es`、`ar`。
- `reference` 跟随主音色语气；`text` 使用 `emotion_text` 描述情绪；`vector` 接受八维情绪配比；`audio` 用 `emotion_voice` 借用另一段参考音频的情绪，不更换主音色。`emotion_alpha` 调节强度；上游对 text/vector 模式将强度限制到 1.0，因此超过 1.0 不会继续增强这两种模式。

## 从旧 TTS 配置切换

新版本不读取 Gemini 配置。部署前在远端原配置上核对差异，获得逐项授权后保留注释并增量修改：删除 `tts.protocol`、`tts.model`、`tts.language_code`、`tts.vertex`；新增 `tts.base_url`、`tts.api_key`；将 `tts.voice` 改为本服务音色 ID，按网络情况调整 `tts.proxy`。`enabled`、`timeout` 和 `max_retries` 可保留原值。先确认服务可达、音色存在，再拉取明确版本的 MyBot 完整镜像并重建容器。
