# RAG向量化工具流水线,提供文本分块与向量化处理功能,注意:此模块不应该直接被外部调用
import asyncio
import itertools
import json
from pathlib import Path
import httpx
import aiofiles
import faiss
import numpy as np
from app.utils.log import get_logger
from .providers import SiliconFlowEmbedding
from .schemas import EmbeddingConfig, VectorizeConfig, VectorStorage

logger = get_logger(__name__)


async def write_to_file(folder_path: str, chunks: list[str]) -> None:
    """将文本块写入debug.txt文件用于调试。"""
    file_path = Path(folder_path) / "debug.txt"
    async with aiofiles.open(file_path, mode="w", encoding="utf-8") as f:
        text = await asyncio.to_thread(lambda: "\n\n".join(chunks))
        await f.write(text)


async def read_txt_file(folder_path: str | Path) -> list[str]:
    """递归读取目录下所有txt文件，返回去除空白后的文本列表。"""
    file_path = Path(folder_path)
    results = file_path.rglob("*.txt")
    text_lst: list[str] = []
    for file in results:
        async with aiofiles.open(file, mode="r", encoding="utf-8") as f:
            data = await f.read()
            text = await asyncio.to_thread(lambda: "".join(data.split()))
            text_lst.append(text)
    return text_lst


def split_text_optimized(
    text_lst: list[str], min_chunk_size: int, max_chunk_size: int
) -> list[str]:
    """按句子边界智能切分文本。

    支持中英文标点作为分隔符，并保持引号配对完整性。

    Args:
        text_lst: 待切分的文本列表。
        min_chunk_size: 最小分块长度，在此之前不会切分。
        max_chunk_size: 最大分块长度，超过时强制切分。

    Returns:
        切分后的文本块列表。
    """
    chunks: list[str] = []
    strong_delimiters = {"。", "！", "？", ".", "!", "?"}
    weak_delimiters = {",", "，", ";", "；"}
    open_quote = "“"
    close_quote = "”"
    for text in text_lst:
        n = len(text)
        if n <= max_chunk_size:
            chunks.append(text)
            continue
        start = 0
        while start < n:
            limit = min(start + max_chunk_size, n)
            quote_count = 0
            fallback_split_idx = -1
            current_split_found = False
            for i in range(start, limit):
                char = text[i]
                if char == open_quote:
                    quote_count += 1
                elif char == close_quote:
                    if quote_count > 0:
                        quote_count -= 1
                if i < start + min_chunk_size:
                    continue
                if quote_count == 0:
                    if char in strong_delimiters:
                        chunks.append(text[start : i + 1])
                        start = i + 1
                        current_split_found = True
                        break
                    elif char in weak_delimiters:
                        fallback_split_idx = i
            if current_split_found:
                continue
            if limit == n:
                chunks.append(text[start:])
                break
            if fallback_split_idx != -1:
                chunks.append(text[start : fallback_split_idx + 1])
                start = fallback_split_idx + 1
            else:
                chunks.append(text[start:limit])
                start = limit
    return chunks


def get_vector_representation(result: dict, chunk: list[str]) -> list[VectorStorage]:
    """将embedding API返回结果与原始文本块组装成VectorStorage列表。"""
    vector_list = []
    data: list[dict] = result.get("data", [])
    if not data:
        raise ValueError("数据为空")
    data.sort(key=lambda x: x["index"])
    for k, v in zip(chunk, data):
        vector = VectorStorage(name=k, vectors=v["embedding"])
        vector_list.append(vector)
    return vector_list


async def write_data(
    folder_str: str | Path,
    result_queue: asyncio.Queue[list[VectorStorage]],
) -> dict[str, list[float]]:
    """从队列消费向量数据并流式写入vector.json文件。"""
    folder_path = Path(folder_str)
    file_path = folder_path / "vector.json"
    first_item = True
    data: dict[str, list[float]] = {}
    async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
        await f.write("{")
        while True:
            vector_list = await result_queue.get()
            if not vector_list:
                result_queue.task_done()
                break
            for item in vector_list:
                if not first_item:
                    await f.write(",")
                else:
                    first_item = False
                key_str = json.dumps(item.name, ensure_ascii=False)
                value_str = json.dumps(item.vectors)
                data.update({item.name: item.vectors})
                line = f"\n{key_str}:{value_str}"
                await f.write(line)
                logger.info("已写入文件")
            result_queue.task_done()
        await f.write("\n}")
    return data


async def store_vectors(data: dict[str, list[float]], directory: str | Path) -> None:
    """构建FAISS索引并持久化存储。

    根据向量数量自动选择索引类型：<=50000使用Flat，否则使用IVF100,Flat。
    使用内积（余弦相似度）作为相似度度量，存储前会对向量进行L2归一化。

    Args:
        data: 文本到向量的映射字典。
        directory: 索引文件存储目录，将生成index.faiss和id_mapping.json。
    """
    str_name = list(data.keys())
    vectors = list(data.values())
    vectors_np = await asyncio.to_thread(lambda: np.array(vectors).astype("float32"))
    vectors_np = np.ascontiguousarray(vectors_np)  # 确保内存连续
    dim = vectors_np.shape[1]
    num_vectors = vectors_np.shape[0]
    await asyncio.to_thread(faiss.normalize_L2, vectors_np)  # 归一化
    index_factory_str = "Flat" if num_vectors <= 50000 else "IVF100,Flat"
    index: faiss.Index = await asyncio.to_thread(
        faiss.index_factory, dim, index_factory_str, faiss.METRIC_INNER_PRODUCT
    )
    if not index.is_trained:
        await asyncio.to_thread(index.train, vectors_np)  # type: ignore
    await asyncio.to_thread(index.add, vectors_np)  # type: ignore
    index_path = Path(directory) / "index.faiss"
    map_path = Path(directory) / "id_mapping.json"
    # await asyncio.to_thread(faiss.write_index, index, str(index_path))
    # 由于faiss直接去访问硬盘写文件时,可能会没有办法处理中文路径
    # 这里把整个index对象压缩并转换成一个numpy(uint8)数组,再转换为二进制数据块,通过python写入
    chunk = await asyncio.to_thread(faiss.serialize_index, index)
    async with aiofiles.open(index_path, "wb") as f:
        await f.write(chunk.tobytes())
    async with aiofiles.open(map_path, "w", encoding="utf-8") as f:
        s = json.dumps(str_name, ensure_ascii=False)
        await f.write(s)


async def producer(
    task_queue: asyncio.Queue[list[str]], chunks: list[str], max_lines: int
) -> None:
    """将文本块按批次放入任务队列。"""
    it = iter(chunks)
    batch_count = 0
    total_items = 0
    while True:
        chunk = list(itertools.islice(it, max_lines))
        if not chunk:
            break
        await task_queue.put(chunk)
        batch_count += 1
        total_items += len(chunk)


async def token_dispenser(
    token_queue: asyncio.Queue[int], tokens_per_minute: int
) -> None:
    """令牌桶限流器，按固定速率发放令牌控制API调用频率。"""
    interval = 60.0 / tokens_per_minute
    token_count = 0
    while True:
        await token_queue.put(1)
        token_count += 1
        await asyncio.sleep(interval)


async def consumer(
    task_queue: asyncio.Queue[list[str]],
    token_queue: asyncio.Queue[int],
    siliconflow_embedding: SiliconFlowEmbedding,
    result_queue: asyncio.Queue[list[VectorStorage]],
    model: str,
) -> None:
    """消费任务队列中的文本块并调用embedding API获取向量。

    失败时会将批次拆分为单条重新入队重试。
    """
    while True:
        chunk: list[str] = await task_queue.get()
        await token_queue.get()
        try:
            result = await siliconflow_embedding.get_embedding(text=chunk, model=model)
            vector_list = await asyncio.to_thread(
                get_vector_representation, result=result, chunk=chunk
            )
            logger.info("已经获取向量表示")
            await result_queue.put(vector_list)
        except Exception:
            logger.warning("达到最大重试次数,即将拆分")
            for c in chunk:
                await task_queue.put([c])  # 重新放入队列重试
        task_queue.task_done()


async def async_process_pipeline(
    chunks: list[str],
    max_lines: int,
    tokens_per_minute: int,
    consumer_count: int,
    siliconflow_embedding: SiliconFlowEmbedding,
    folder_path: str | Path,
    model: str,
) -> dict[str, list[float]]:
    """向量化异步处理流水线。

    采用生产者-消费者模式，通过令牌桶限流，支持多消费者并发处理。

    Args:
        chunks: 待向量化的文本块列表。
        max_lines: 每批次最大文本块数量。
        tokens_per_minute: 每分钟允许的API调用次数。
        consumer_count: 消费者协程数量。
        siliconflow_embedding: embedding服务客户端。
        folder_path: 结果文件存储目录。
        model: embedding模型名称。

    Returns:
        文本到向量的映射字典。
    """
    task_queue = asyncio.Queue()
    token_queue = asyncio.Queue(maxsize=1)
    result_queue = asyncio.Queue()
    consumers: list[asyncio.Task] = []

    token_task = asyncio.create_task(
        token_dispenser(token_queue=token_queue, tokens_per_minute=tokens_per_minute)
    )
    for _ in range(consumer_count):
        con = asyncio.create_task(
            consumer(
                task_queue=task_queue,
                token_queue=token_queue,
                siliconflow_embedding=siliconflow_embedding,
                result_queue=result_queue,
                model=model,
            )
        )
        consumers.append(con)
    writer_task = asyncio.create_task(
        write_data(folder_str=folder_path, result_queue=result_queue)
    )
    producer_task = asyncio.create_task(
        producer(task_queue=task_queue, chunks=chunks, max_lines=max_lines)
    )
    await producer_task
    await task_queue.join()
    token_task.cancel()
    for c in consumers:
        c.cancel()
    await result_queue.put(None)
    result = await writer_task
    return result


async def search_vectors(
    query_vector: list[float],
    directory: str,
    top_k: int = 5,
) -> list[str]:
    """从FAISS索引中检索最相似的top_k个文本。"""
    index_path = Path(directory) / "index.faiss"
    map_path = Path(directory) / "id_mapping.json"
    async with aiofiles.open(map_path, "r", encoding="utf-8") as f:
        content = await f.read()
        id_mapping: list[str] = json.loads(content)
    async with aiofiles.open(index_path, "rb") as f:
        index_bytes = await f.read()
    index: faiss.Index = await asyncio.to_thread(
        lambda: faiss.deserialize_index(np.frombuffer(index_bytes, dtype=np.uint8))
    )
    query_np = np.array([query_vector], dtype="float32")
    await asyncio.to_thread(faiss.normalize_L2, query_np)  # 归一化
    distances: np.ndarray
    indices: np.ndarray
    distances, indices = await asyncio.to_thread(index.search, query_np, top_k)  # type: ignore
    results = [id_mapping[idx] for idx in indices[0] if idx != -1]
    return results


async def vectorize_text(
    folder_str: str,
    vectorize_config: VectorizeConfig,
    siliconflow_embedding: SiliconFlowEmbedding,
    model: str,
) -> None:
    """文本向量化入口函数。

    读取目录下的txt文件，分块后向量化，最终构建FAISS索引存储。

    Args:
        folder_str: 包含txt文件的源目录路径。
        vectorize_config: 向量化配置参数。
        siliconflow_embedding: embedding服务客户端。
        model: embedding模型名称。
    """
    folder_path = Path(folder_str)
    vector_dir = folder_path.parent / "vector"
    vector_dir.mkdir(parents=True, exist_ok=True)
    text_lst = await read_txt_file(folder_path=folder_path)
    chunks = await asyncio.to_thread(
        split_text_optimized,
        text_lst=text_lst,
        min_chunk_size=vectorize_config.min_chunk_size,
        max_chunk_size=vectorize_config.max_chunk_size,
    )
    data = await async_process_pipeline(
        chunks=chunks,
        max_lines=vectorize_config.max_line,
        tokens_per_minute=vectorize_config.tokens_per_minute,
        consumer_count=vectorize_config.consumer_count,
        siliconflow_embedding=siliconflow_embedding,
        folder_path=vector_dir,
        model=model,
    )
    await store_vectors(data=data, directory=vector_dir)


async def start() -> None:
    """主函数入口，用于直接运行向量化流水线。"""

    # 获取用户输入
    source_folder = input("请输入源文件目录路径（包含txt文件的目录）: ").strip()
    api_key = input("请输入 Embedding API Key: ").strip()
    base_url = input(
        "请输入 API Base URL（默认: https://api.siliconflow.cn/v1/embeddings）: "
    ).strip()
    model_name = input("请输入模型名称（默认: BAAI/bge-large-zh-v1.5）: ").strip()

    # 设置默认值
    if not base_url:
        base_url = "https://api.siliconflow.cn/v1/embeddings"
    if not model_name:
        model_name = "BAAI/bge-large-zh-v1.5"

    vectorize_config = VectorizeConfig()
    embedding_config = EmbeddingConfig(
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
        provider_type="siliconflow",
        retry_count=3,
        retry_delay=2,
    )

    async with httpx.AsyncClient(timeout=60.0) as client:
        siliconflow_embedding = SiliconFlowEmbedding(
            embedding_config=embedding_config,
            client=client,
        )

        logger.info(f"开始处理目录: {source_folder}")
        await vectorize_text(
            folder_str=source_folder,
            vectorize_config=vectorize_config,
            siliconflow_embedding=siliconflow_embedding,
            model=model_name,
        )
        logger.info("向量化处理完成!")


if __name__ == "__main__":
    asyncio.run(start())
