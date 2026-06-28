"""
文件整体功能：代理连通性测试工具，并发检测代理列表对 B 站的可达性。
所属模块：util.proxy
依赖文件：
    - util.proxy.ProxyManager.ProxyManager（代理解析与应用）
    - loguru（日志输出）
    - requests（HTTP 请求）
对外能力：
    1. 提供 ProxyTester 类，支持单代理与代理列表的并发连通性测试；
    2. 提供 test_proxy_connectivity 快捷函数，直接返回格式化文本结果。
"""
import time
import requests
import loguru
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from util.proxy.ProxyManager import ProxyManager


# 代理连通性测试工具
class ProxyTester:
    """
    代理连通性测试器。

    类设计作用：通过实际 HTTP/HTTPS 请求验证代理是否能访问 B 站接口，
                并尝试获取出口 IP 信息。
    存储属性：
        timeout (int)：单次请求超时秒数。
    承担业务：在配置页面为用户提供代理可用性检测与结果展示。
    """

    def __init__(self, timeout: int = 10):
        """
        初始化测试器。

        参数：
            timeout (int)：单次请求超时秒数，默认 10。
        返回值：无。
        内部逻辑：保存 timeout。
        调用位置：test_proxy_connectivity 或用户单独创建测试器时调用。
        """
        self.timeout = timeout

    # 测试单个代理连通性
    def test_single_proxy(self, proxy: str) -> Dict[str, Any]:
        """
        测试单个代理的连通性。

        参数：
            proxy (str)：代理字符串，如 "http://host:port" 或 "none"。
        返回值：dict[str, Any]，包含 proxy、status、response_time、error、ip_info 的结果字典。
        内部逻辑：
            1. 初始化结果为 failed；
            2. 校验非直连代理格式；
            3. 应用代理到临时 Session；
            4. 先尝试 HTTP，再尝试 HTTPS 访问 B 站 nav 接口；
            5. 成功时获取出口 IP 信息；
            6. 分类捕获超时、代理错误、连接错误等异常。
        调用位置：test_proxy_list 在线程池中并发调用。
        """
        result = {
            "proxy": ProxyManager.mask_proxy_value(proxy) or proxy,
            "status": "failed",
            "response_time": None,
            "error": None,
            "ip_info": None,
        }

        try:
            session = requests.Session()
            proxy = ProxyManager.normalize_proxy_value(proxy)
            if proxy != "none" and not self._validate_proxy_format(proxy):
                result["error"] = "代理格式无效"
                return result
            ProxyManager(proxy).apply_to_session(session)

            # 先测试 HTTP 连通性
            try:
                start_time = time.time()
                response = session.get(
                    "http://api.bilibili.com/x/web-interface/nav",
                    timeout=self.timeout,
                )
                end_time = time.time()
                if response.status_code == 200:
                    result["status"] = "success"
                    result["response_time"] = round((end_time - start_time) * 1000, 2)
                    result["ip_info"] = self._get_ip_info(session)
                    return result
            except Exception:
                pass

            # HTTP 失败则测试 HTTPS
            start_time = time.time()
            response = session.get(
                "https://api.bilibili.com/x/web-interface/nav",
                timeout=self.timeout,
                headers={
                    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
                },
            )
            end_time = time.time()
            response_time = round((end_time - start_time) * 1000, 2)  # 毫秒

            if response.status_code == 200:
                result["status"] = "success"
                result["response_time"] = response_time
                # 获取出口IP信息
                result["ip_info"] = self._get_ip_info(session)
            else:
                result["error"] = f"B站连接失败: HTTP {response.status_code}"
                result["status"] = "partial"
                result["response_time"] = response_time
                # 部分成功时也尝试获取IP
                result["ip_info"] = self._get_ip_info(session)
        except requests.exceptions.Timeout:
            result["error"] = f"连接超时 (>{self.timeout}s)"
        except requests.exceptions.ProxyError as e:
            result["error"] = f"代理错误: {str(e)[:100]}"
        except requests.exceptions.ConnectionError as e:
            error_msg = str(e)
            if "SOCKS" in error_msg:
                result["error"] = f"SOCKS连接失败，请检查代理地址和认证信息"
            elif "proxy" in error_msg.lower():
                result["error"] = "代理连接失败"
            else:
                result["error"] = f"网络连接失败: {error_msg[:100]}"
        except Exception as e:
            result["error"] = f"未知错误: {str(e)}"

        return result

    def _get_ip_info(self, session) -> str:
        """
        获取当前代理的出口 IP 信息。

        参数：
            session (requests.Session)：已应用代理的 Session。
        返回值：str，包含 IP、城市与 ISP 的文本；全部失败返回 "IP获取失败"。
        内部逻辑：
            1. 按优先级依次请求 ip-api.com、httpbin.org、ip.sb；
            2. 解析各服务返回的 JSON 并格式化；
            3. 任一服务成功即返回，否则返回失败提示。
        调用位置：test_single_proxy 在请求成功后调用。
        """
        # 服务列表：优先详细信息，然后降级到基础服务
        ip_services = [
            {
                "name": "ip-api.com",
                "url": "http://ip-api.com/json/?fields=query,city,isp",
                "parser": lambda data: (
                    f"{data.get('query', '未知')} ({data.get('city', '未知')}, {data.get('isp', '未知')})"
                ),
            },
            {
                "name": "httpbin.org",
                "url": "http://httpbin.org/ip",
                "parser": lambda data: data.get("origin", "未知"),
            },
            {
                "name": "ip.sb",
                "url": "https://api.ip.sb/geoip",
                "parser": lambda data: (
                    f"{data.get('ip', '未知')} ({data.get('city', '未知')}, {data.get('asn_organization', '未知')})"
                ),
            },
        ]

        for service in ip_services:
            try:
                ip_response = session.get(service["url"], timeout=8)
                if ip_response.status_code == 200:
                    ip_data = ip_response.json()
                    return service["parser"](ip_data)
            except Exception:
                continue

        return "IP获取失败"

    # 验证代理格式是否正确
    def _validate_proxy_format(self, proxy: str) -> bool:
        """
        校验代理字符串格式。

        参数：
            proxy (str)：代理字符串。
        返回值：bool，格式有效返回 True，否则 False。
        内部逻辑：
            1. 检查非空；
            2. 检查是否以 http/https/socks5/socks4 开头；
            3. 检查地址部分是否包含端口。
        调用位置：test_single_proxy 中对非直连代理进行预校验。
        """
        try:
            # 基本格式检查
            if not proxy or proxy.strip() == "":
                return False

            # 检查是否包含协议
            if not any(
                proxy.startswith(protocol)
                for protocol in ["http://", "https://", "socks5://", "socks4://"]
            ):
                return False

            # 检查是否包含端口
            if ":" not in proxy.split("://")[1]:
                return False

            return True
        except Exception:
            return False

    # 测试代理列表的连通性
    def test_proxy_list(
        self, proxy_string: str, max_workers: int = 5
    ) -> List[Dict[str, Any]]:
        """
        并发测试代理列表中每个代理的连通性。

        参数：
            proxy_string (str)：逗号分隔的代理字符串。
            max_workers (int)：并发线程数，默认 5。
        返回值：list[dict[str, Any]]，按原始顺序排序的测试结果列表。
        内部逻辑：
            1. 解析代理列表，若为空则默认包含 none；
            2. 使用 ThreadPoolExecutor 并发调用 test_single_proxy；
            3. 收集结果并按原始顺序排序（直连在前，其余按原列表顺序）。
        调用位置：test_proxy_connectivity 中调用，也可由 UI 直接调用。
        """
        proxy_list = ProxyManager.parse_proxy_list(
            proxy_string,
            include_direct_fallback=True,
        )
        if not proxy_list:
            proxy_list = ["none"]

        results = []
        # 使用线程池并发测试
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_proxy = {
                executor.submit(self.test_single_proxy, proxy): proxy
                for proxy in proxy_list
            }

            for future in as_completed(future_to_proxy):
                try:
                    result = future.result()
                    results.append(result)
                    loguru.logger.info(
                        f"代理测试完成: {result['proxy']} - {result['status']}"
                    )
                except Exception as e:
                    loguru.logger.error(f"代理测试异常: {e}")

        # 按照原始顺序排序结果（直连在前，然后是代理）
        def get_sort_key(result):
            proxy = result["proxy"]
            if proxy == "直连" or proxy.lower() in ["none", "direct"]:
                return (0, proxy)
            else:
                try:
                    raw_proxy = next(
                        (
                            item
                            for item in proxy_list
                            if ProxyManager.mask_proxy_value(item) == proxy
                        ),
                        proxy,
                    )
                    return (1, proxy_list.index(raw_proxy))
                except ValueError:
                    return (2, proxy)

        results.sort(key=get_sort_key)
        return results

    # 格式化测试结果为可读文本
    def format_test_results(self, results: List[Dict[str, Any]]) -> str:
        """
        将测试结果格式化为可读文本。

        参数：
            results (list[dict[str, Any]])：test_proxy_list 返回的结果列表。
        返回值：str，带 emoji 与统计信息的测试报告文本。
        内部逻辑：
            1. 遍历每条结果，根据 status 拼接不同图标与信息；
            2. 统计成功数量；
            3. 返回完整报告字符串。
        调用位置：test_proxy_connectivity 与 UI 展示时调用。
        """
        output = []
        output.append("代理连通性测试结果:")
        output.append("=" * 50)

        success_count = 0
        for i, result in enumerate(results, 1):
            proxy = result["proxy"]
            status = result["status"]
            response_time = result["response_time"]
            error = result["error"]
            ip_info = result["ip_info"]

            if status == "success":
                output.append(f"✅ [{i}] {proxy}")
                output.append(f"    响应时间: {response_time}ms")
                if ip_info and ip_info != "IP获取失败":
                    output.append(f"    出口IP: {ip_info}")
                success_count += 1
            elif status == "partial":
                output.append(f"⚠️  [{i}] {proxy}")
                output.append(f"    响应时间: {response_time}ms")
                if ip_info and ip_info != "IP获取失败":
                    output.append(f"    出口IP: {ip_info}")
                output.append(f"    警告: {error}")
            else:
                output.append(f"❌ [{i}] {proxy}")
                output.append(f"    错误: {error}")

            output.append("")

        output.append("=" * 50)
        output.append(f"测试统计: {success_count}/{len(results)} 个代理可用")
        return "\n".join(output)


def test_proxy_connectivity(proxy_string: str = "none", timeout: int = 10) -> str:
    """
    测试代理连通性的快捷函数。

    参数：
        proxy_string (str)：逗号分隔的代理字符串，默认 "none"。
        timeout (int)：单次请求超时秒数，默认 10。
    返回值：str，格式化后的测试结果文本。
    内部逻辑：创建 ProxyTester，调用 test_proxy_list 与 format_test_results。
    调用位置：配置页面“测试代理”按钮等需要直接拿到文本报告的场景。
    """
    tester = ProxyTester(timeout=timeout)
    results = tester.test_proxy_list(proxy_string)
    return tester.format_test_results(results)
