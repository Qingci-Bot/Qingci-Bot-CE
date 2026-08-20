"""SSRF 防护工具

市场索引、插件归档下载等由远端来源控制的 URL 拉取必须经过本模块校验，
阻止把服务端作为跳板探测/访问内网（含云元数据 169.254.169.254 等链路本地地址）。
"""

from __future__ import annotations

import ipaddress
import urllib.request
from urllib.parse import urlparse

_ALLOWED_GIT_PREFIXES = (
    "https://",
    "http://",
    "ssh://",
    "git@",
    "git+https://",
    "git+ssh://",
)


def is_private_host(hostname: str | None) -> bool:
    """判断主机名是否为 IP 字面量且属于私网/环回/链路本地/保留地址

    域名（如 api.example.com）不在此拦截（DNS 解析结果的上游策略由调用方决定），
    仅拦截可直接识别的 IP 字面量，避免破坏合法域名场景。
    """
    if not hostname:
        return False
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast


def is_private_url(url: str) -> bool:
    """URL 是否指向私网/环回/链路本地地址（基于 IP 字面量）"""
    return is_private_host(urlparse(url).hostname)


def is_allowed_git_url(url: str) -> bool:
    """git 克隆 URL 白名单：仅允许标准传输协议

    拒绝 ``ext::``（可执行任意命令）与 ``file:`` 等非网络协议，防止恶意仓库
    URL 借 git 传输协议实现命令执行/本地文件读取。
    """
    low = url.lower()
    if "ext::" in low or "file:" in low:
        return False
    return url.startswith(_ALLOWED_GIT_PREFIXES)


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """禁止自动跟随 HTTP 重定向（防 302 跳转到内网地址）"""

    def redirect_request(
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,  # noqa: ANN001
    ) -> None:
        return None
