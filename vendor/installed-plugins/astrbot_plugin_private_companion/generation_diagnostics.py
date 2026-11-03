"""Bounded task-local diagnostics for proactive text generation."""
from __future__ import annotations
from contextvars import ContextVar
from functools import wraps

_trace=ContextVar('companion_generation_diagnostics',default=None)

def category(error):
    text=str(error or '').lower()
    if 'oauth provider is closed' in text or 'oauth 插件服务已关闭' in text:
        return '模型运行实例已关闭（OAuth provider is closed）'
    if 'invalid schema' in text or 'invalid_function_parameters' in text:
        return '模型工具参数格式不兼容'
    if 'daily_token' in text or 'token_budget' in text:return '模型调用被每日预算限制跳过'
    if 'backoff' in text:return '模型调用仍在故障退避等待中'
    if 'timeout' in text or '超时' in text:return '模型请求超时'
    if '429' in text or 'rate limit' in text:return '模型服务限流'
    if '401' in text or '403' in text or 'authentication' in text:return '模型授权未通过'
    if 'empty_response' in text:return '模型调用成功但未返回可见正文'
    return '模型请求失败（详细原因见服务日志）'

def record(owner, error, *, task=''):
    trace=_trace.get()
    if trace is None or trace['owner'] is not owner:return
    label=category(error)
    if label not in trace['errors']:trace['errors'].append(label)
    del trace['errors'][:-4]

def reasons():
    trace=_trace.get()
    return list(trace['errors']) if trace else []

def trace_generation(function):
    @wraps(function)
    async def wrapped(self,*args,**kwargs):
        token=_trace.set({'owner':self,'errors':[]})
        try:return await function(self,*args,**kwargs)
        finally:_trace.reset(token)
    return wrapped
