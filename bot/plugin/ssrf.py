"""SSRF 防护工具

市场索引、插件归档下载等由远端来源控制的 URL 拉取必须经过本模块校验，
阻止把服务端作为跳板探测/访问内网（含云元数据 169.254.169.254 等链路本地地址）。

防御覆盖：
- IP 字面量直接校验（含十进制/十六进制/八进制混淆形式）
- 域名：连接前解析全部 A/AAAA 记录，任一为内网地址即判定（缓解 DNS rebinding）
- HTTP 重定向禁用（防 302 跳内网）
- git 克隆 URL 传输协议白名单（拒绝 ext:: 命令执行与 file: 协议）
"""

from __future__ import annotations

import ipaddress
import socket
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


IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def _is_private_ip(ip: IPAddress) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _parse_ip_literal(hostname: str) -> IPAddress | None:
    """解析 IP 字面量，兼容十进制 / 十六进制 / 八进制混淆形式"""
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        pass
    # 混淆形式：整数表示的 IPv4（如 http://2130706433/ = 127.0.0.1）
    for base in (0, 8, 16):
        try:
            val = int(hostname, base)
        except (ValueError, TypeError):
            continue
        try:
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
        except (ipaddress.AddressValueError, ValueError):
            continue
        break
    return None


def _resolve_ips(hostname: str) -> list[IPAddress] | None:
    """解析域名全部 A/AAAA 记录；解析失败返回 None"""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return None
    ips: list[IPAddress] = []
    for info in infos:
        try:
            ips.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    return ips


def is_private_host(hostname: str | None) -> bool:
    """判断主机名是否指向私网/环回/链路本地/保留地址

    - IP 字面量（含混淆形式）直接判定
    - 域名：解析全部 A/AAAA 记录，任一为内网即判定
    """
    if not hostname:
        return False
    ip = _parse_ip_literal(hostname)
    if ip is not None:
        return _is_private_ip(ip)
    # 域名：解析校验（解析失败按安全处理——由连接层报错，不判定私网）
    ips = _resolve_ips(hostname)
    if not ips:
        return False
    return any(_is_private_ip(ip) for ip in ips)


def is_private_url(url: str) -> bool:
    """URL 是否指向私网/环回/链路本地地址"""
    parsed = urlparse(url)
    host = parsed.hostname
    # scp 风格 git@host:path —— urlparse 无法提取 hostname
    if not host and url.startswith("git@") and ":" in url:
        host = url.split("@", 1)[1].split(":", 1)[0]
    return is_private_host(host)


def is_allowed_git_url(url: str) -> bool:
    """git 克隆 URL 白名单：仅允许标准传输协议

    拒绝 ``ext::``（可执行任意命令）与 ``file:`` 等非网络协议，防止恶意仓库
    URL 借 git 传输协议实现命令执行/本地文件读取。
    """
    low = url.lower()
    # ext:: 是 git 的任意命令传输协议（URL 以 ext:: 开头）；file: 读取本地仓库
    if low.startswith("ext::") or "file:" in low:
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
