"""Page endpoints for bounded companion archives and import preflight."""
from __future__ import annotations
import asyncio
from quart import request
from .portability.service import service
from .logging_util import get_module_logger

logger=get_module_logger(__name__)


class PortabilityPageMixin:
    async def portable_dispatch(self,operation):
        try:
            payload=await request.get_json(silent=True) or {} if request.method=='POST' else {}
            if not isinstance(payload,dict):raise ValueError('请求必须是JSON对象')
            coordinator=service(self)
            if operation=='status':result=await coordinator.status()
            elif operation=='inspect':result=coordinator.inspect(payload.get('archive_id'))
            elif operation=='export':result=await coordinator.export(payload)
            elif operation=='upload/start':result=coordinator.begin_upload(payload.get('bytes'))
            elif operation=='upload/chunk':result=await coordinator.upload(payload.get('archive_id'),payload.get('offset'),payload.get('data'))
            elif operation=='download':result=coordinator.download(str(request.args.get('archive_id') or ''),int(request.args.get('offset',0)))
            elif operation=='preview':result=await coordinator.preview(payload.get('archive_id'),payload)
            elif operation=='apply':result=await coordinator.apply(payload.get('archive_id'),payload)
            elif operation=='rollback':result=await coordinator.rollback(payload.get('transaction_id'))
            elif operation=='resume':result=await coordinator.resume(payload.get('scope'))
            else:raise ValueError('未知迁移操作')
            return self._ok(result)
        except asyncio.CancelledError:raise
        except (ValueError,FileNotFoundError) as exc:return self._error(str(exc),status_code=400)
        except Exception as exc:
            logger.error('迁移操作失败 operation=%s type=%s',operation,type(exc).__name__)
            return self._error('迁移操作失败：'+type(exc).__name__+'；请查看日志及迁移备份状态',status_code=500)

    async def portable_status(self):return await self.portable_dispatch('status')
    async def portable_export(self):return await self.portable_dispatch('export')
    async def portable_upload_start(self):return await self.portable_dispatch('upload/start')
    async def portable_upload_chunk(self):return await self.portable_dispatch('upload/chunk')
    async def portable_download(self):return await self.portable_dispatch('download')
    async def portable_preview(self):return await self.portable_dispatch('preview')
    async def portable_apply(self):return await self.portable_dispatch('apply')
    async def portable_rollback(self):return await self.portable_dispatch('rollback')
    async def portable_resume(self):return await self.portable_dispatch('resume')

    async def portable_inspect(self):return await self.portable_dispatch('inspect')
