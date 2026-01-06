"""NovelAI 图像生成 Payload 模型"""

from pydantic import BaseModel, Field


class Center(BaseModel):
    """坐标中心点"""

    x: float
    """X轴坐标"""
    y: float
    """Y轴坐标"""


class CharCaption(BaseModel):
    """角色标题"""

    char_caption: str
    """角色描述文本，需要以boy/girl等起手"""
    centers: list[Center]
    """角色在画面中的坐标位置列表"""


class V4Caption(BaseModel):
    """V4版本标题配置"""

    base_caption: str
    """基础标题描述"""
    char_captions: list[CharCaption] = Field(default_factory=list)
    """角色标题列表"""


class V4Prompt(BaseModel):
    """V4版本提示词配置"""

    caption: V4Caption
    """标题配置"""
    use_coords: bool = False
    """是否在V4提示词中使用坐标"""
    use_order: bool = True
    """是否在V4提示词中使用顺序"""


class V4NegativePrompt(BaseModel):
    """V4版本负面提示词配置"""

    caption: V4Caption
    """负面标题配置"""
    legacy_uc: bool = False
    """是否使用旧版未分类模式"""


class DirectorReferenceDescription(BaseModel):
    """导演参考描述"""

    caption: V4Caption
    """参考描述标题"""
    legacy_uc: bool = False
    """是否使用旧版未分类模式"""


class CharacterPrompt(BaseModel):
    """角色提示词"""

    prompt: str
    """角色正面提示词"""
    uc: str
    """角色负面提示词（Undesired Content）"""
    center: Center
    """角色中心坐标"""
    enabled: bool
    """是否启用此角色提示"""


class Parameters(BaseModel):
    """生成参数配置"""

    params_version: int = 3
    """参数版本号"""
    width: int
    """生成图片的宽度（像素）"""
    height: int
    """生成图片的高度（像素）"""
    scale: int = 10
    """CFG缩放值，控制生成质量"""
    sampler: str = "k_euler_ancestral"
    """采样器类型"""
    steps: int = 28
    """采样步数，影响生成质量和时间"""
    n_samples: int = 1
    """生成图片的数量"""
    ucPreset: int = 0
    """未分类提示词预设"""
    qualityToggle: bool = True
    """质量切换开关"""
    autoSmea: bool = False
    """自动SMEA功能开关"""
    dynamic_thresholding: bool = True
    """动态阈值处理开关"""
    controlnet_strength: int = 1
    """ControlNet强度"""
    legacy: bool = False
    """是否使用旧版模式"""
    add_original_image: bool = True
    """是否添加原始图片"""
    cfg_rescale: float = 0.7
    """CFG重缩放参数"""
    noise_schedule: str = "karras"
    """噪声调度类型"""
    legacy_v3_extend: bool = False
    """旧版V3扩展功能"""
    skip_cfg_above_sigma: float | None = None
    """在指定sigma值以上跳过CFG"""
    use_coords: bool = False
    """是否使用坐标信息"""
    normalize_reference_strength_multiple: bool = True
    """标准化参考强度倍数"""
    inpaintImg2ImgStrength: int = 1
    """图像修复强度"""
    v4_prompt: V4Prompt
    """V4版本提示词配置"""
    v4_negative_prompt: V4NegativePrompt
    """V4版本负面提示词配置"""
    legacy_uc: bool = False
    """旧版未分类模式（全局）"""
    seed: int
    """随机种子，用于生成可重现的结果"""
    characterPrompts: list[CharacterPrompt] = Field(default_factory=list)
    """角色提示词列表"""
    negative_prompt: str = ""
    """负面提示词"""
    deliberate_euler_ancestral_bug: bool = False
    """故意保留Euler祖先采样器的错误"""
    prefer_brownian: bool = True
    """偏好布朗运动采样"""
    director_reference_images: list[str] | None = None
    """导演参考图像列表（base64编码）"""
    director_reference_descriptions: list[DirectorReferenceDescription] | None = None
    """导演参考描述列表"""
    director_reference_information_extracted: list[int] | None = None
    """参考信息提取状态值"""
    director_reference_strength_values: list[int] | None = None
    """参考强度值"""


class NovelAIPayload(BaseModel):
    """NovelAI API请求载荷"""

    model: str
    """使用的AI模型名称"""
    action: str
    """执行的操作类型"""
    parameters: Parameters
    """生成参数配置"""


def get_payload(
    prompt: str,
    new_negative_prompt: str,
    width: int,
    height: int,
    seed: int,
    v4_prompt_char_captions: list[CharCaption] | None = None,
    image_base64: str | None = None,
    model: str = "nai-diffusion-4-5-full",
) -> NovelAIPayload:
    """
    生成NovelAI API请求载荷

    Args:
        prompt: 正面提示词
        new_negative_prompt: 负面提示词
        width: 图片宽度
        height: 图片高度
        seed: 随机种子
        v4_prompt_char_captions: 角色标题列表，格式为:
            [{"char_caption":"正面提示词", "centers":[{"x":0.5,"y":0.5}]}, ...]
            正面提示词需要boy或者girl起手
        image_base64: base64编码的参考图像
        model: 使用的AI模型名称

    Returns:
        NovelAIPayload: 类型安全的请求载荷对象
    """
    # 初始化V4提示词
    v4_prompt_caption = V4Caption(base_caption=prompt, char_captions=[])

    v4_negative_caption = V4Caption(base_caption=new_negative_prompt, char_captions=[])

    # 初始化角色提示词列表
    character_prompts: list[CharacterPrompt] = []

    # 判断是否使用坐标
    use_coords = False

    # 处理角色标题
    if v4_prompt_char_captions:
        use_coords = True
        for char_caption in v4_prompt_char_captions:
            # 添加正面角色标题
            v4_prompt_caption.char_captions.append(char_caption)

            # 添加负面角色标题（空提示词，但保持相同坐标）
            negative_char_caption = CharCaption(
                char_caption="", centers=char_caption.centers
            )
            v4_negative_caption.char_captions.append(negative_char_caption)

            # 添加角色提示词
            character_prompt = CharacterPrompt(
                prompt=char_caption.char_caption,
                uc="",
                center=char_caption.centers[0],
                enabled=True,
            )
            character_prompts.append(character_prompt)

    # 构建V4提示词对象
    v4_prompt = V4Prompt(
        caption=v4_prompt_caption, use_coords=use_coords, use_order=True
    )

    v4_negative_prompt = V4NegativePrompt(caption=v4_negative_caption, legacy_uc=False)

    # 构建参数对象
    parameters = Parameters(
        width=width,
        height=height,
        seed=seed,
        use_coords=use_coords,
        v4_prompt=v4_prompt,
        v4_negative_prompt=v4_negative_prompt,
        characterPrompts=character_prompts,
    )

    # 处理参考图像
    if image_base64:
        reference_caption = V4Caption(base_caption="character&style", char_captions=[])
        reference_description = DirectorReferenceDescription(
            caption=reference_caption, legacy_uc=False
        )

        parameters.director_reference_images = [image_base64]
        parameters.director_reference_descriptions = [reference_description]
        parameters.director_reference_information_extracted = [1]
        parameters.director_reference_strength_values = [1]

    # 构建并返回完整载荷
    payload = NovelAIPayload(model=model, action="generate", parameters=parameters)

    return payload
