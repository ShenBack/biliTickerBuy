"""
文件说明：
- 文件整体功能：演示如何通过 youdaili.com 的代理 IP 接口获取代理 IP 列表。
- 所属模块：demo 示例目录，仅作为第三方代理 API 调用的参考示例，不直接参与主业务逻辑。
- 依赖文件：依赖第三方库 requests；接口地址为 youdaili.com 的 V1 代理获取接口。
- 对外能力：脚本运行后向控制台打印代理 API 的原始响应文本。
"""

import requests

# youdaili 代理 IP 获取接口 URL，各查询参数（app_key、count、protocol 等）需按实际账号信息填写
url = "http://api.youdaili.com/v1/proxy/get?app_key=&app_secret=&count=&format=&protocol=&sep=&expire=&auth=&isp=&province=&city=&only="

# 请求体与请求头，当前示例中为空
payload = {}
headers = {}

# 发起 GET 请求并获取响应
response = requests.request("GET", url, headers=headers, data=payload)

# 在控制台输出代理 API 返回的原始文本
print(response.text)
