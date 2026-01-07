from typing import Annotated, Literal

from pydantic import BaseModel, Field


class ScrapeLocation(BaseModel):
    country: Annotated[
        str | None,
        Field(description="国家代码，使用ISO 3166-1 alpha-2标准，例如：'US', 'CN'"),
    ] = None
    languages: Annotated[list[str] | None, Field(description="首选语言列表")] = None


class ScrapeAction(BaseModel):
    type: Literal[
        "wait", "screenshot", "scroll", "click", "write", "press", "executeJavascript"
    ]
    milliseconds: int | None = None
    selector: str | None = None
    text: str | None = None
    direction: Literal["up", "down"] | None = None
    script: str | None = None


class BaseScrapeConfig(BaseModel):
    """
    基础抓取配置，不包含 URL。
    用于 Search 的 scrape_options 以及 ScrapeConfig 的基类。
    """

    formats: Annotated[
        list[Literal["markdown", "html", "rawHtml", "links", "screenshot", "json"]],
        Field(description="输出格式列表"),
    ] = ["markdown"]

    only_main_content: Annotated[bool, Field(description="是否只提取主要内容")] = True

    include_tags: Annotated[
        list[str] | None, Field(description="包含的标签/选择器")
    ] = None
    exclude_tags: Annotated[
        list[str] | None, Field(description="排除的标签/选择器")
    ] = None

    headers: Annotated[dict[str, str] | None, Field(description="自定义Headers")] = None

    wait_for: Annotated[int | str, Field(description="等待时间(ms)或CSS选择器")] = 0

    mobile: Annotated[bool, Field(description="模拟移动设备")] = False
    skip_tls_verification: Annotated[bool, Field(description="跳过SSL验证")] = False
    remove_base64_images: Annotated[bool, Field(description="移除Base64图片")] = True

    location: Annotated[ScrapeLocation | None, Field(description="地理位置设置")] = None
    actions: Annotated[list[ScrapeAction] | None, Field(description="交互操作")] = None

    fast_mode: Annotated[bool, Field(description="快速模式(无图/CSS)")] = False
    block_ads: Annotated[bool, Field(description="屏蔽广告")] = True

    proxy: Annotated[
        Literal["basic", "stealth", "auto"] | None,
        Field(description="代理模式"),
    ] = None

    timeout: Annotated[int, Field(description="超时时间(ms)")] = 30000


class Scrape(BaseScrapeConfig):
    """
    单页抓取模型，必须包含 URL
    """

    url: Annotated[str, Field(description="需要抓取的网页URL地址")]


class Search(BaseModel):
    query: Annotated[str, Field(description="搜索关键词")]
    limit: Annotated[int, Field(description="返回结果数量限制")] = 5

    tbs: Annotated[str | None, Field(description="时间范围，如 'qdr:w' (过去一周)")] = (
        None
    )

    lang: Annotated[str | None, Field(description="语言代码，如 'en'")] = None
    country: Annotated[str | None, Field(description="国家代码，如 'us'")] = None

    location: Annotated[
        str | None, Field(description="通用位置参数(若不使用 lang/country)")
    ] = None

    sources: Annotated[
        list[Literal["web", "images", "news"]], Field(description="搜索来源")
    ] = ["web"]

    scrape_options: Annotated[
        BaseScrapeConfig | None, Field(description="对搜索结果页面的抓取配置")
    ] = None


class Map(BaseModel):
    url: Annotated[str, Field(description="目标网站URL")]
    search: Annotated[str | None, Field(description="仅包含匹配此字符串的URL")] = None
    include_subdomains: Annotated[bool | None, Field(description="是否包含子域名")] = (
        None
    )
    limit: Annotated[int | None, Field(description="最大返回URL数量")] = None

    sitemap: Annotated[
        Literal["only", "include", "skip"] | None,
        Field(description="Sitemap处理策略：'only'仅使用sitemap，'include'包含sitemap，'skip'跳过sitemap"),
    ] = None

    timeout: Annotated[int | None, Field(description="超时时间(ms)")] = None


class Firecrawl(BaseModel):
    scrape: Annotated[Scrape | None, Field(description="抓取配置")] = None
    search: Annotated[Search | None, Field(description="搜索配置")] = None
    map: Annotated[Map | None, Field(description="网站地图映射配置")] = None
