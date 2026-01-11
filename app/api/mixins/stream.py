# """流式操作 Mixin 类"""

# from typing import AsyncGenerator

# from app.models.api.payloads import stream as stream_payload
# from app.models.events.response import Response
# from .base import BaseMixin


# class StreamMixin(BaseMixin):
#     """流式操作相关的 API 接口"""

#     async def clean_stream_temp_file(self) -> Response:
#         """清理流临时文件"""
#         echo = self._generate_echo()
#         payload = stream_payload.CleanStreamTempFilePayload(
#             params=stream_payload.CleanStreamTempFileParams(),
#             echo=echo,
#         )
#         return await self._send_and_wait(payload)

#     async def test_download_stream(
#         self, error: bool = False
#     ) -> AsyncGenerator[Response, None]:
#         """流式下载测试"""
#         echo = self._generate_echo()
#         payload = stream_payload.TestDownloadStreamPayload(
#             params=stream_payload.TestDownloadStreamParams(error=error),
#             echo=echo,
#         )
#         await self._send_payload(payload)
#         async for chunk in self.wait_stream(echo):
#             if (
#                 isinstance(chunk.data, StreamDataChunk)
#                 and chunk.data.data_type == "data_chunk"
#             ):
#                 yield chunk

#     async def upload_file_stream(
#         self,
#         stream_id: str,
#         chunk_data: str | None = None,
#         chunk_index: int | None = None,
#         total_chunks: int | None = None,
#         file_size: int | None = None,
#         expected_sha256: str | None = None,
#         is_complete: bool | None = None,
#         filename: str | None = None,
#         reset: bool | None = None,
#         verify_only: bool | None = None,
#         file_retention: int = 300000,
#     ) -> Response:
#         """流式上传文件"""
#         echo = self._generate_echo()
#         payload = stream_payload.UploadFileStreamPayload(
#             params=stream_payload.UploadFileStreamParams(
#                 stream_id=stream_id,
#                 chunk_data=chunk_data,
#                 chunk_index=chunk_index,
#                 total_chunks=total_chunks,
#                 file_size=file_size,
#                 expected_sha256=expected_sha256,
#                 is_complete=is_complete,
#                 filename=filename,
#                 reset=reset,
#                 verify_only=verify_only,
#                 file_retention=file_retention,
#             ),
#             echo=echo,
#         )
#         return await self._send_and_wait(payload)

#     async def download_file_stream(
#         self,
#         file: str | None = None,
#         file_id: str | None = None,
#         chunk_size: int = 65536,
#         **kwargs,
#     ) -> AsyncGenerator[Response, None]:
#         """流式下载文件"""
#         echo = self._generate_echo()
#         payload = stream_payload.DownloadFileStreamPayload(
#             params=stream_payload.DownloadFileStreamParams(
#                 file=file,
#                 file_id=file_id,
#                 chunk_size=chunk_size,
#             ),
#             echo=echo,
#         )
#         await self._send_payload(payload)
#         async for chunk in self.wait_stream(echo):
#             if isinstance(chunk.data, StreamDataChunk) and chunk.data.data_type in {
#                 "file_chunk",
#                 "data_chunk",
#             }:
#                 yield chunk

#     async def download_file_record_stream(
#         self,
#         file: str | None = None,
#         file_id: str | None = None,
#         chunk_size: int = 65536,
#         out_format: str | None = None,
#     ) -> AsyncGenerator[Response, None]:
#         """流式下载语音文件"""
#         echo = self._generate_echo()
#         payload = stream_payload.DownloadFileRecordStreamPayload(
#             params=stream_payload.DownloadFileRecordStreamParams(
#                 file=file,
#                 file_id=file_id,
#                 chunk_size=chunk_size,
#                 out_format=out_format,
#             ),
#             echo=echo,
#         )
#         await self._send_payload(payload)
#         async for chunk in self.wait_stream(echo):
#             if isinstance(chunk.data, StreamDataChunk) and chunk.data.data_type in {
#                 "file_chunk",
#                 "data_chunk",
#             }:
#                 yield chunk

#     async def download_file_image_stream(
#         self,
#         file: str | None = None,
#         file_id: str | None = None,
#         chunk_size: int = 65536,
#     ) -> AsyncGenerator[Response, None]:
#         """流式下载图片"""
#         echo = self._generate_echo()
#         payload = stream_payload.DownloadFileImageStreamPayload(
#             params=stream_payload.DownloadFileImageStreamParams(
#                 file=file,
#                 file_id=file_id,
#                 chunk_size=chunk_size,
#             ),
#             echo=echo,
#         )
#         await self._send_payload(payload)
#         async for chunk in self.wait_stream(echo):
#             if isinstance(chunk.data, StreamDataChunk) and chunk.data.data_type in {
#                 "file_chunk",
#                 "data_chunk",
#             }:
#                 yield chunk
