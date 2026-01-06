"""
AI 图像处理工具模块

该模块提供了用于处理 NovelAI 图像的实用函数，包括:
- 计算符合 NovelAI 规范的图像尺寸
- 获取图像尺寸信息
- 重新编码图像以符合 NovelAI 格式要求
"""

import base64
import io
import math
from typing import Tuple
from PIL import Image
from PIL.Image import Image as PILImage


def calculate_novelai_reference_dimensions(width: int, height: int) -> Tuple[int, int]:
    """
    根据输入图片的宽高计算符合 NovelAI 官方标准的目标尺寸
    
    该函数根据输入图片的宽高比，自动选择合适的目标宽高比和像素总数，
    然后计算出符合 64 像素对齐要求的最终尺寸。
    
    Args:
        width: 原始图片宽度（像素）
        height: 原始图片高度（像素）
    
    Returns:
        Tuple[int, int]: 返回 (目标宽度, 目标高度)，单位为像素
        
    Note:
        - 接近正方形（0.91-1.1 比例）: 目标 1:1，2166784 像素
        - 横向图片（>1.1 比例）: 目标 3:2，1572864 像素  
        - 纵向图片（<0.91 比例）: 目标 2:3，1572864 像素
        - 最终尺寸会向下取整到最接近的 64 的倍数
    """
    # 宽高比阈值，用于判断图片是否接近正方形
    aspect_ratio_threshold = 1.1
    
    # 计算原始图片的宽高比
    original_aspect_ratio = width / height
    
    # 根据宽高比选择合适的目标宽高比和像素总数
    match original_aspect_ratio:
        # 接近正方形的图片 (宽高比在 0.91-1.1 之间)
        case r if 1 / aspect_ratio_threshold < r < aspect_ratio_threshold:
            target_aspect_ratio = 1.0  # 目标宽高比 1:1
            target_pixels = 2166784    # 目标像素总数约 1472x1472
            
        # 横向图片 (宽度大于高度)
        case r if r >= aspect_ratio_threshold:
            target_aspect_ratio = 1.5  # 目标宽高比 3:2
            target_pixels = 1572864    # 目标像素总数约 1536x1024
            
        # 纵向图片 (高度大于宽度)
        case _:
            target_aspect_ratio = 2 / 3  # 目标宽高比 2:3
            target_pixels = 1572864      # 目标像素总数约 1024x1536
    
    # 根据目标像素总数和宽高比计算理想高度
    # 公式推导: width * height = target_pixels 且 width/height = target_aspect_ratio
    # 因此: height = sqrt(target_pixels / target_aspect_ratio)
    ideal_height = math.sqrt(target_pixels / target_aspect_ratio)
    
    # 根据理想高度和目标宽高比计算理想宽度
    ideal_width = ideal_height * target_aspect_ratio
    
    # 将宽度和高度向下取整到最接近的 64 的倍数
    # NovelAI 要求图像尺寸必须是 64 的倍数
    final_width = (int(ideal_width) // 64) * 64
    final_height = (int(ideal_height) // 64) * 64
    
    return final_width, final_height


def get_image_dimensions(b64_string: str) -> Tuple[PILImage, Tuple[int, int]]:
    """
    从 Base64 编码的字符串中解析图片并获取其尺寸
    
    Args:
        b64_string: Base64 编码的图片字符串
    
    Returns:
        Tuple[PILImage, Tuple[int, int]]: 返回 (PIL图片对象, (宽度, 高度))
        
    Raises:
        base64.binascii.Error: 如果 Base64 字符串格式无效
        PIL.UnidentifiedImageError: 如果无法识别图片格式
    """
    # 将 Base64 字符串解码为二进制数据
    img_data = base64.b64decode(b64_string)
    
    # 创建字节流对象，用于从内存中读取图片数据
    image_stream = io.BytesIO(img_data)
    
    # 使用 PIL 打开图片
    img = Image.open(image_stream)
    
    # 返回图片对象和尺寸信息 (width, height)
    return img, img.size


def reencode_image(b64_string: str) -> str:
    """
    将图片重新编码为符合 NovelAI 官方规范的格式
    
    该函数模拟 NovelAI 官网的图片处理流程:
    1. 解码 Base64 图片
    2. 计算符合标准的目标尺寸
    3. 使用高质量算法调整图片大小
    4. 转换为 RGBA 格式
    5. 保存为无压缩的 PNG 格式
    6. 重新编码为 Base64
    
    Args:
        b64_string: Base64 编码的原始图片字符串
    
    Returns:
        str: Base64 编码的处理后图片字符串
        
    Note:
        - 使用 LANCZOS 重采样算法确保高质量缩放
        - 输出格式强制为 RGBA PNG
        - PNG 压缩级别设为 0（无压缩）以匹配官方行为
    """
    # 获取图片对象和原始尺寸
    image, (width, height) = get_image_dimensions(b64_string)
    
    # 根据原始尺寸计算符合 NovelAI 标准的目标尺寸
    final_width, final_height = calculate_novelai_reference_dimensions(width, height)
    
    # 使用 Lanczos 算法调整图片大小（高质量重采样）
    # LANCZOS 是一种高质量的下采样滤波器，适合缩小图片
    resized_image = image.resize((final_width, final_height), Image.Resampling.LANCZOS)
    
    # 确保图片为 RGBA 模式（4 通道：红、绿、蓝、透明度）
    if resized_image.mode != "RGBA":
        resized_image = resized_image.convert("RGBA")
    
    # 将图片保存为 PNG 格式的字节流
    with io.BytesIO() as output:
        # compress_level=0 表示不压缩，保持最高质量
        # 这是为了匹配 NovelAI 官网的处理方式
        resized_image.save(output, format="PNG", compress_level=0)
        
        # 获取字节流中的数据
        final_png_bytes = output.getvalue()
    
    # 将 PNG 字节数据编码为 Base64 字符串
    base64_encoded = base64.b64encode(final_png_bytes).decode("utf-8")
    
    return base64_encoded

