import hashlib
import hmac
import sys
import time
from copy import copy
from datetime import datetime, timedelta
from typing import Callable

from pytz import timezone, utc
from vnpy_evo.event import EventEngine
from vnpy_evo.trader.constant import (
    Direction,
    Exchange,
    Interval,
    OrderType,
    Product,
    Status,
    Offset,
    OptionType
)
from vnpy_evo.trader.gateway import BaseGateway
from vnpy_evo.trader.object import (
    AccountData,
    BarData,
    CancelRequest,
    ContractData,
    HistoryRequest,
    OrderData,
    OrderRequest,
    PositionData,
    SubscribeRequest,
    TickData,
    TradeData
)
from vnpy_rest import Request, RestClient
from vnpy_websocket import WebsocketClient


# 中国时区
CHINA_TZ: timezone = timezone("Asia/Shanghai")

# Mainnet server hosts
MAINNET_REST_HOST: str = "https://api.bybit.com"
MAINNET_PRIVATE_WEBSOCKET_HOST: str = "wss://stream.bybit.com/v5/private"
MAINNET_SPOT_WEBSOCKET_HOST: str = "wss://stream.bybit.com/v5/public/spot"
MAINNET_LINEAR_WEBSOCKET_HOST: str = "wss://stream.bybit.com/v5/public/linear"
MAINNET_INVERSE_WEBSOCKET_HOST: str = "wss://stream.bybit.com/v5/public/inverse"
MAINNET_OPTION_WEBSOCKET_HOST: str = "wss://stream.bybit.com/v5/public/option"

# Testnet server hosts
TESTNET_REST_HOST: str = "https://api-testnet.bybit.com"
TESTNET_PRIVATE_WEBSOCKET_HOST: str = "wss://stream-testnet.bybit.com/v5/private"
TESTNET_SPOT_WEBSOCKET_HOST: str = "wss://stream-testnet.bybit.com/v5/public/spot"
TESTNET_LINEAR_WEBSOCKET_HOST: str = "wss://stream-testnet.bybit.com/v5/public/linear"
TESTNET_INVERSE_WEBSOCKET_HOST: str = "wss://stream-testnet.bybit.com/v5/public/inverse"
TESTNET_OPTION_WEBSOCKET_HOST: str = "wss://stream-testnet.bybit.com/v5/public/option"


# Product type map
PRODUCT_BYBIT2VT: dict[str, Exchange] = {
    "spot": Product.SPOT,
    "linear": Product.SWAP,
    "inverse": Product.SWAP,
    "option": Product.OPTION,
}

# Option type map
OPTION_TYPE_BYBIT2VT: dict[str, OptionType] = {
    "Call": OptionType.CALL,
    "Put": OptionType.PUT
}

# 委托状态映射
STATUS_BYBIT2VT: dict[str, Status] = {
    "Created": Status.NOTTRADED,
    "New": Status.NOTTRADED,
    "PartiallyFilled": Status.PARTTRADED,
    "Filled": Status.ALLTRADED,
    "Cancelled": Status.CANCELLED,
    "Rejected": Status.REJECTED,
}

# 委托类型映射
ORDER_TYPE_VT2BYBIT: dict[OrderType, str] = {
    OrderType.LIMIT: "Limit",
    OrderType.MARKET: "Market",
}
ORDER_TYPE_BYBIT2VT: dict[str, OrderType] = {v: k for k, v in ORDER_TYPE_VT2BYBIT.items()}

# 买卖方向映射
DIRECTION_VT2BYBIT: dict[Direction, str] = {Direction.LONG: "Buy", Direction.SHORT: "Sell"}
DIRECTION_BYBIT2VT: dict[str, Direction] = {v: k for k, v in DIRECTION_VT2BYBIT.items()}

# 数据频率映射
INTERVAL_VT2BYBIT: dict[Interval, str] = {
    Interval.MINUTE: "1",
    Interval.HOUR: "60",
    Interval.DAILY: "D",
    Interval.WEEKLY: "W",
}
TIMEDELTA_MAP: dict[Interval, timedelta] = {
    Interval.MINUTE: timedelta(minutes=1),
    Interval.HOUR: timedelta(hours=1),
    Interval.DAILY: timedelta(days=1),
    Interval.WEEKLY: timedelta(days=7),
}

# 反向永续合约类型列表
swap_symbols: set[str] = set()

# 反向交割合约类型列表
futures_symbols: set[str] = set()

# USDT永续合约类型列表
usdt_symbols: set[str] = set()

# 本地委托号缓存集合
local_orderids: set[str] = set()


class BybitGateway(BaseGateway):
    """
    The Bybit trading gateway for VeighNa.
    """

    default_name = "BYBIT"

    default_setting: dict[str, str] = {
        "API Key": "",
        "Secret Key": "",
        "Server": ["MAINNET", "TESTNET"],
        "Proxy Host": "",
        "Proxy Port": "",
    }

    default_name: str = "BYBIT"

    exchanges: list[Exchange] = [Exchange.BYBIT]

    def __init__(self, event_engine: EventEngine, gateway_name: str) -> None:
        """
        The init method of the gateway.

        event_engine: the global event engine object of VeighNa
        gateway_name: the unique name for identifying the gateway
        """
        super().__init__(event_engine, gateway_name)

        self.rest_api: BybitRestApi = BybitRestApi(self)
        self.private_ws_api: BybitPrivateWebsocketApi = None
        self.public_ws_api: BybitPublicWebsocketApi = None

    def connect(self, setting: dict) -> None:
        """Start server connections"""
        key: str = setting["API Key"]
        secret: str = setting["Secret Key"]
        server: str = setting["Server"]
        proxy_host: str = setting["Proxy Host"]
        proxy_port: str = setting["Proxy Port"]

        if proxy_port.isdigit():
            proxy_port = int(proxy_port)
        else:
            proxy_port = 0

        self.rest_api.connect(
            key,
            secret,
            server,
            proxy_host,
            proxy_port
        )
        # self.private_ws_api.connect(
        #     key,
        #     secret,
        #     server,
        #     proxy_host,
        #     proxy_port
        # )
        # self.public_ws_api.connect(
        #     server,
        #     proxy_host,
        #     proxy_port
        # )

    def subscribe(self, req: SubscribeRequest) -> None:
        """Subscribe market data"""
        self.public_ws_api.subscribe(req)

    def send_order(self, req: OrderRequest) -> str:
        """Send new order"""
        return self.rest_api.send_order(req)

    def cancel_order(self, req: CancelRequest):
        """Cancel existing order"""
        self.rest_api.cancel_order(req)

    def query_account(self) -> None:
        """Not required since Bybit provides websocket update"""
        pass

    def query_position(self) -> None:
        """Not required since Bybit provides websocket update"""
        return

    def query_history(self, req: HistoryRequest) -> list[BarData]:
        """Query kline history data"""
        return self.rest_api.query_history(req)

    def close(self) -> None:
        """Close server connections"""
        self.rest_api.stop()
        # self.private_ws_api.stop()
        # self.public_ws_api.stop()


class BybitRestApi(RestClient):
    """The REST API of BybitGateway"""

    def __init__(self, gateway: BybitGateway) -> None:
        """
        The init method of the api.

        gateway: the parent gateway object for pushing callback data.
        """
        super().__init__()

        self.gateway: BybitGateway = gateway
        self.gateway_name: str = gateway.gateway_name

        self.key: str = ""
        self.secret: bytes = b""

        self.order_count: int = 0

    def sign(self, request: Request) -> Request:
        """Standard callback for signing a request"""
        if request.method == "GET":
            api_params: dict = request.params
            if api_params is None:
                api_params = request.params = {}
        else:
            api_params: dict = request.data
            if api_params is None:
                api_params = request.data = {}

        api_params["api_key"] = self.key
        api_params["recv_window"] = 30 * 1000
        api_params["timestamp"] = generate_timestamp(-5)

        data2sign = "&".join([f"{k}={v}" for k, v in sorted(api_params.items())])
        signature: str = sign(self.secret, data2sign.encode())
        api_params["sign"] = signature

        return request

    def new_orderid(self) -> str:
        """Generate local order id"""
        prefix: str = datetime.now().strftime("%Y%m%d-%H%M%S-")

        self.order_count += 1
        suffix: str = str(self.order_count).rjust(8, "0")

        orderid: str = prefix + suffix
        return orderid

    def check_error(self, name: str, data: dict) -> bool:
        """回报状态检查"""
        if data["ret_code"]:
            error_code: int = data["ret_code"]
            error_msg: str = data["ret_msg"]
            msg = f"{name}失败，错误代码：{error_code}，信息：{error_msg}"
            self.gateway.write_log(msg)
            return True

        return False

    def connect(
        self,
        key: str,
        secret: str,
        server: str,
        proxy_host: str,
        proxy_port: int,
    ) -> None:
        """Start server connection"""
        self.key = key
        self.secret = secret.encode()

        if server == "MAINNET":
            self.init(MAINNET_REST_HOST, proxy_host, proxy_port)
        else:
            self.init(TESTNET_REST_HOST, proxy_host, proxy_port)

        self.start()
        self.gateway.write_log("REST API started")

        self.query_contract()

    def send_order(self, req: OrderRequest) -> str:
        """委托下单"""
        # 检查委托类型是否正确
        if req.type not in ORDER_TYPE_VT2BYBIT:
            self.gateway.write_log(f"委托失败，不支持的委托类型：{req.type.value}")
            return

        # 检查合约代码是否正确并根据合约类型判断下单接口
        if req.symbol in usdt_symbols:
            path: str = "/private/linear/order/create"
        else:
            self.gateway.write_log(f"委托失败，找不到该合约代码{req.symbol}")
            return

        # 生成本地委托号
        orderid: str = self.new_orderid()
        # 推送提交中事件
        order: OrderData = req.create_order_data(orderid, self.gateway_name)

        # 生成委托请求
        data: dict = {
            "symbol": req.symbol,
            "side": DIRECTION_VT2BYBIT[req.direction],
            "qty": req.volume,
            "order_link_id": orderid,
            "time_in_force": "GoodTillCancel",
            "reduce_only": False,
            "close_on_trigger": False
        }

        data["order_type"] = ORDER_TYPE_VT2BYBIT[req.type]
        data["price"] = req.price

        if req.offset == Offset.CLOSE:
            data["reduce_only"] = True

        self.add_request(
            "POST",
            path,
            callback=self.on_send_order,
            data=data,
            extra=order,
            on_failed=self.on_send_order_failed,
            on_error=self.on_send_order_error,
        )

        self.gateway.on_order(order)
        return order.vt_orderid

    def on_send_order_failed(
        self,
        status_code: int,
        request: Request
    ) -> None:
        """委托下单失败服务器报错回报"""
        order: OrderData = request.extra
        order.status = Status.REJECTED
        self.gateway.on_order(order)

        data: dict = request.response.json()
        error_msg: str = data["ret_msg"]
        error_code: int = data["ret_code"]
        msg = f"委托失败，错误代码:{error_code},  错误信息：{error_msg}"
        self.gateway.write_log(msg)

    def on_send_order_error(
        self,
        exception_type: type,
        exception_value: Exception,
        tb,
        request: Request
    ) -> None:
        """委托下单回报函数报错回报"""
        order: OrderData = request.extra
        order.status = Status.REJECTED
        self.gateway.on_order(order)

        if not issubclass(exception_type, ConnectionError):
            self.on_error(exception_type, exception_value, tb, request)

    def on_send_order(self, data: dict, request: Request) -> None:
        """委托下单回报"""
        if self.check_error("委托下单", data):
            order: OrderData = request.extra
            order.status = Status.REJECTED
            self.gateway.on_order(order)

    def cancel_order(self, req: CancelRequest) -> None:
        """委托撤单"""
        # 检查合约代码是否正确并根据合约类型判断撤单接口
        if req.symbol in usdt_symbols:
            path: str = "/private/linear/order/cancel"
        else:
            self.gateway.write_log(f"撤单失败，找不到该合约代码{req.symbol}")
            return

        data: dict = {"symbol": req.symbol}

        # 检查是否为本地委托号
        if req.orderid in local_orderids:
            data["order_link_id"] = req.orderid
        else:
            data["order_id"] = req.orderid

        self.add_request(
            "POST",
            path,
            data=data,
            callback=self.on_cancel_order
        )

    def on_cancel_order(self, data: dict, request: Request) -> None:
        """委托撤单回报"""
        if self.check_error("委托撤单", data):
            return

    def on_failed(self, status_code: int, request: Request) -> None:
        """处理请求失败回报"""
        data: dict = request.response.json()
        error_msg: str = data["ret_msg"]
        error_code: int = data["ret_code"]

        msg = f"请求失败，状态码：{request.status}，错误代码：{error_code}, 信息：{error_msg}"
        self.gateway.write_log(msg)

    def on_error(
        self,
        exception_type: type,
        exception_value: Exception,
        tb,
        request: Request
    ) -> None:
        """触发异常回报"""
        msg = f"触发异常，状态码：{exception_type}，信息：{exception_value}"
        self.gateway.write_log(msg)

        sys.stderr.write(
            self.exception_detail(exception_type, exception_value, tb, request)
        )

    def on_query_position(self, data: dict, request: Request) -> None:
        """持仓查询回报"""
        if self.check_error("查询持仓", data):
            return

        data = data["result"]

        for d in data:
            d = d["data"]

            if d["size"]:
                position: PositionData = PositionData(
                    symbol=d["symbol"],
                    exchange=Exchange.BYBIT,
                    direction=DIRECTION_BYBIT2VT[d["side"]],
                    volume=d["size"],
                    price=d["entry_price"],
                    gateway_name=self.gateway_name
                )
                self.gateway.on_position(position)

    def on_query_contract(self, data: dict, request: Request) -> None:
        """Callback of available contracts query"""
        result: dict = data["result"]

        category: str = result["category"]
        product: Product = PRODUCT_BYBIT2VT[category]

        for d in result["list"]:
            contract: ContractData = ContractData(
                symbol=d["symbol"],
                exchange=Exchange.BYBIT,
                name=d["symbol"],
                product=product,
                size=1,
                pricetick=float(d["priceFilter"]["tickSize"]),
                min_volume=float(d["lotSizeFilter"]["minOrderQty"]),
                history_data=True,
                gateway_name=self.gateway_name
            )

            # If symbol contains digit, then should be futures
            if product == Product.SWAP and not contract.symbol.isalpha():
                contract.product = Product.FUTURES

            # Add extra option field
            if product == Product.OPTION:
                buf: list = contract.symbol.split("-")

                contract.option_strike = int(buf[2])
                contract.option_underlying = "-".join(buf[:2])
                contract.option_type = OPTION_TYPE_BYBIT2VT[d["optionsType"]]
                contract.option_listed = generate_datetime_2(float(d["launchTime"]) / 1000)
                contract.option_expiry = generate_datetime_2(float(d["deliveryTime"]) / 1000)
                contract.option_portfolio = buf[0]
                contract.option_index = str(contract.option_strike)

            self.gateway.on_contract(contract)

        self.gateway.write_log(f"Available {category} contracts data is received")
        # self.query_position()
        # self.query_account()
        # self.query_order()

    def on_query_account(self, data: dict, request: Request) -> None:
        """资金查询回报"""
        if self.check_error("查询账号", data):
            return

        for key, value in data["result"].items():
            if key == "USDT":
                account: AccountData = AccountData(
                    accountid=key,
                    balance=value["wallet_balance"],
                    frozen=value["used_margin"],
                    gateway_name=self.gateway_name,
                )
                self.gateway.on_account(account)
                self.gateway.write_log(f"{key}资金信息查询成功")

    def on_query_order(self, data: dict, request: Request):
        """未成交委托查询回报"""
        if self.check_error("查询委托", data):
            return

        if not data["result"]:
            return

        for d in data["result"]:
            orderid: str = d["order_link_id"]
            if orderid:
                local_orderids.add(orderid)
            else:
                orderid: str = d["order_id"]

            dt: datetime = generate_datetime(d["created_time"])

            order: OrderData = OrderData(
                symbol=d["symbol"],
                exchange=Exchange.BYBIT,
                orderid=orderid,
                type=ORDER_TYPE_BYBIT2VT[d["order_type"]],
                direction=DIRECTION_BYBIT2VT[d["side"]],
                price=d["price"],
                volume=d["qty"],
                traded=d["cum_exec_qty"],
                status=STATUS_BYBIT2VT[d["order_status"]],
                datetime=dt,
                gateway_name=self.gateway_name
            )
            offset: bool = d["reduce_only"]
            if offset:
                order.offset = Offset.CLOSE
            else:
                order.offset = Offset.OPEN

            self.gateway.on_order(order)

        self.gateway.write_log(f"{order.symbol}委托信息查询成功")

    def query_contract(self) -> None:
        """Query available contract"""
        for category in ["spot", "linear", "inverse", "option"]:
            params: dict = {
                "category": category,
                "limit": 1000
            }

            self.add_request(
                "GET",
                "/v5/market/instruments-info",
                self.on_query_contract,
                params=params
            )

    def query_order(self) -> None:
        """查询未成交委托"""
        path_usdt: str = "/private/linear/order/search"

        for symbol in usdt_symbols:
            params: dict = {
                "symbol": symbol
            }

            self.add_request(
                "GET",
                path_usdt,
                callback=self.on_query_order,
                params=params
            )

    def query_history(self, req: HistoryRequest) -> list[BarData]:
        """查询历史数据"""
        history: list = []
        count: int = 200
        start_time: int = int(req.start.timestamp())

        path: str = "/public/linear/kline"

        while True:
            # 创建查询参数
            params: dict = {
                "symbol": req.symbol,
                "interval": INTERVAL_VT2BYBIT[req.interval],
                "from": start_time,
                "limit": count
            }

            # 从服务器获取响应
            resp = self.request(
                "GET",
                path,
                params=params
            )

            # 如果请求失败则终止循环
            if resp.status_code // 100 != 2:
                msg = f"获取历史数据失败，状态码：{resp.status_code}，信息：{resp.text}"
                self.gateway.write_log(msg)
                break
            else:
                data: dict = resp.json()

                ret_code: int = data["ret_code"]
                if ret_code:
                    ret_msg: str = data["ret_msg"]
                    msg = f"获取历史数据出错，错误信息：{ret_msg}"
                    self.gateway.write_log(msg)
                    break

                if not data["result"]:
                    msg = f"获取历史数据为空，开始时间：{start_time}，数量：{count}"
                    self.gateway.write_log(msg)
                    break

                buf: list = []
                for d in data["result"]:
                    dt: datetime = generate_datetime_2(d["open_time"])

                    bar: BarData = BarData(
                        symbol=req.symbol,
                        exchange=req.exchange,
                        datetime=dt,
                        interval=req.interval,
                        volume=float(d["volume"]),
                        open_price=float(d["open"]),
                        high_price=float(d["high"]),
                        low_price=float(d["low"]),
                        close_price=float(d["close"]),
                        gateway_name=self.gateway_name
                    )
                    buf.append(bar)

                history.extend(buf)

                begin: datetime = buf[0].datetime
                end: datetime = buf[-1].datetime
                msg = f"获取历史数据成功，{req.symbol} - {req.interval.value}，{begin} - {end}"
                self.gateway.write_log(msg)

                # 收到最后数据则结束循环
                if len(buf) < count:
                    break

                # 更新开始时间
                start_time: int = int((bar.datetime + TIMEDELTA_MAP[req.interval]).timestamp())

        return history


class BybitPublicWebsocketApi(WebsocketClient):
    """正向合约的行情Websocket接口"""

    def __init__(self, gateway: BybitGateway) -> None:
        """构造函数"""
        super().__init__()

        self.gateway: BybitGateway = gateway
        self.gateway_name: str = gateway.gateway_name

        self.callbacks: dict[str, Callable] = {}
        self.ticks: dict[str, TickData] = {}
        self.subscribed: dict[str, SubscribeRequest] = {}

        self.symbol_bids: dict[str, dict] = {}
        self.symbol_asks: dict[str, dict] = {}

    def connect(
        self,
        server: str,
        proxy_host: str,
        proxy_port: int
    ) -> None:
        """连接Websocket公共频道"""
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.server = server

        if self.server == "REAL":
            url = PUBLIC_WEBSOCKET_HOST
        else:
            url = TESTNET_PUBLIC_WEBSOCKET_HOST

        self.init(url, self.proxy_host, self.proxy_port)
        self.start()

    def on_connected(self) -> None:
        """连接成功回报"""
        self.gateway.write_log("行情Websocket API连接成功")

        if self.subscribed:
            for req in self.subscribed.values():
                # 重新订阅请求
                self.subscribe_topic(f"instrument_info.100ms.{req.symbol}", self.on_tick)
                self.subscribe_topic(f"orderBookL2_25.{req.symbol}", self.on_depth)

    def on_disconnected(self) -> None:
        """连接断开回报"""
        self.gateway.write_log("行情Websocket API连接断开")

    def subscribe(self, req: SubscribeRequest) -> None:
        """订阅行情"""

        if req.symbol in self.subscribed:
            return

        # 缓存订阅记录
        self.subscribed[req.symbol] = req

        # 创建TICK对象
        tick: TickData = TickData(
            symbol=req.symbol,
            exchange=req.exchange,
            datetime=datetime.now(),
            name=req.symbol,
            gateway_name=self.gateway_name
        )
        self.ticks[req.symbol] = tick

        # 发送订阅请求
        self.subscribe_topic(f"instrument_info.100ms.{req.symbol}", self.on_tick)
        self.subscribe_topic(f"orderBookL2_25.{req.symbol}", self.on_depth)

    def subscribe_topic(
        self,
        topic: str,
        callback: Callable[[str, dict], object]
    ) -> None:
        """订阅公共频道推送"""
        self.callbacks[topic] = callback

        req: dict = {
            "op": "subscribe",
            "args": [topic],
        }
        self.send_packet(req)

    def on_packet(self, packet: dict) -> None:
        """推送数据回报"""
        if "topic" not in packet:
            op: str = packet["request"]["op"]
            if op == "auth":
                self.on_login(packet)
        else:
            channel: str = packet["topic"]
            callback: callable = self.callbacks[channel]
            callback(packet)

    def on_error(
        self,
        exception_type: type,
        exception_value: Exception,
        tb
    ) -> None:
        """触发异常回报"""
        msg = f"触发异常，状态码：{exception_type}，信息：{exception_value}"
        self.gateway.write_log(msg)

        sys.stderr.write(self.exception_detail(
            exception_type, exception_value, tb))

    def on_tick(self, packet: dict) -> None:
        """行情推送回报"""
        topic: str = packet["topic"]
        type_: str = packet["type"]
        data: dict = packet["data"]

        symbol: str = topic.replace("instrument_info.100ms.", "")
        tick: TickData = self.ticks[symbol]

        if type_ == "snapshot":
            if not data["last_price"]:           # 过滤最新价为0的数据
                return

            tick.last_price = float(data["last_price"])

            tick.volume = int(data["volume_24h_e8"]) / 100000000

            tick.datetime = generate_datetime(data["updated_at"])

        else:
            update: dict = data["update"][0]

            if "last_price" not in update:      # 过滤最新价为0的数据
                return

            tick.last_price = float(update["last_price"])

            if update["volume_24h_e8"]:

                tick.volume = int(update["volume_24h_e8"]) / 100000000

            tick.datetime = generate_datetime(update["updated_at"])

        self.gateway.on_tick(copy(tick))

    def on_depth(self, packet: dict) -> None:
        """盘口推送回报"""
        topic: str = packet["topic"]
        type_: str = packet["type"]
        data: dict = packet["data"]
        if not data:
            return

        symbol: str = topic.replace("orderBookL2_25.", "")
        tick: TickData = self.ticks[symbol]
        bids: dict = self.symbol_bids.setdefault(symbol, {})
        asks: dict = self.symbol_asks.setdefault(symbol, {})

        if type_ == "snapshot":

            buf: list = data["order_book"]

            for d in buf:
                price: float = float(d["price"])

                if d["side"] == "Buy":
                    bids[price] = d
                else:
                    asks[price] = d
        else:
            for d in data["delete"]:
                price: float = float(d["price"])

                if d["side"] == "Buy":
                    bids.pop(price)
                else:
                    asks.pop(price)

            for d in (data["update"] + data["insert"]):

                price: float = float(d["price"])
                if d["side"] == "Buy":
                    bids[price] = d
                else:
                    asks[price] = d

        bid_keys: list = list(bids.keys())
        bid_keys.sort(reverse=True)

        ask_keys: list = list(asks.keys())
        ask_keys.sort()

        for i in range(5):
            n = i + 1

            bid_price = bid_keys[i]
            bid_data = bids[bid_price]
            ask_price = ask_keys[i]
            ask_data = asks[ask_price]

            setattr(tick, f"bid_price_{n}", bid_price)
            setattr(tick, f"bid_volume_{n}", bid_data["size"])
            setattr(tick, f"ask_price_{n}", ask_price)
            setattr(tick, f"ask_volume_{n}", ask_data["size"])

        tick.datetime = generate_datetime_2(int(packet["timestamp_e6"]) / 1000000)
        self.gateway.on_tick(copy(tick))


class BybitPrivateWebsocketApi(WebsocketClient):
    """正向合约的交易Websocket接口"""

    def __init__(self, gateway: BybitGateway) -> None:
        """构造函数"""
        super().__init__()

        self.gateway: BybitGateway = gateway
        self.gateway_name: str = gateway.gateway_name

        self.key: str = ""
        self.secret: bytes = b""
        self.server: str = ""

        self.callbacks: dict[str, Callable] = {}
        self.ticks: dict[str, TickData] = {}
        self.subscribed: dict[str, SubscribeRequest] = {}

        self.symbol_bids: dict[str, dict] = {}
        self.symbol_asks: dict[str, dict] = {}

    def connect(
        self,
        key: str,
        secret: str,
        server: str,
        proxy_host: str,
        proxy_port: int
    ) -> None:
        """连接Websocket私有频道"""
        self.key = key
        self.secret = secret.encode()
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.server = server

        if self.server == "REAL":
            url = PRIVATE_WEBSOCKET_HOST
        else:
            url = TESTNET_PRIVATE_WEBSOCKET_HOST

        self.init(url, self.proxy_host, self.proxy_port)
        self.start()

    def login(self) -> None:
        """用户登录"""
        expires: int = generate_timestamp(30)
        msg = f"GET/realtime{int(expires)}"
        signature: str = sign(self.secret, msg.encode())

        req: dict = {
            "op": "auth",
            "args": [self.key, expires, signature]
        }
        self.send_packet(req)

    def subscribe_topic(
        self,
        topic: str,
        callback: Callable[[str, dict], object]
    ) -> None:
        """订阅私有频道"""
        self.callbacks[topic] = callback

        req: dict = {
            "op": "subscribe",
            "args": [topic],
        }
        self.send_packet(req)

    def on_connected(self) -> None:
        """连接成功回报"""
        self.gateway.write_log("交易Websocket API连接成功")
        self.login()

    def on_disconnected(self) -> None:
        """连接断开回报"""
        self.gateway.write_log("交易Websocket API连接断开")

    def on_packet(self, packet: dict) -> None:
        """推送数据回报"""
        if "topic" not in packet:
            op: str = packet["request"]["op"]
            if op == "auth":
                self.on_login(packet)
        else:
            channel: str = packet["topic"]
            callback: callable = self.callbacks[channel]
            callback(packet)

    def on_error(
        self,
        exception_type: type,
        exception_value: Exception,
        tb
    ) -> None:
        """触发异常回报"""
        msg = f"触发异常，状态码：{exception_type}，信息：{exception_value}"
        self.gateway.write_log(msg)

        sys.stderr.write(self.exception_detail(
            exception_type, exception_value, tb))

    def on_login(self, packet: dict):
        """用户登录请求回报"""
        success: bool = packet.get("success", False)
        if success:
            self.gateway.write_log("交易Websocket API登录成功")

            self.subscribe_topic("order", self.on_order)
            self.subscribe_topic("execution", self.on_trade)
            self.subscribe_topic("position", self.on_position)
            self.subscribe_topic("wallet", self.on_account)

        else:
            self.gateway.write_log("交易Websocket API登录失败")

    def on_account(self, packet: dict) -> None:
        """资金更新推送"""
        for d in packet["data"]:
            account = AccountData(
                accountid="USDT",
                balance=d["wallet_balance"],
                frozen=d["wallet_balance"] - d["available_balance"],
                gateway_name=self.gateway_name,
            )
            self.gateway.on_account(account)

    def on_trade(self, packet: dict) -> None:
        """成交更新推送"""
        for d in packet["data"]:
            orderid: str = d["order_link_id"]
            if not orderid:
                orderid: str = d["order_id"]

            trade: TradeData = TradeData(
                symbol=d["symbol"],
                exchange=Exchange.BYBIT,
                orderid=orderid,
                tradeid=d["exec_id"],
                direction=DIRECTION_BYBIT2VT[d["side"]],
                price=float(d["price"]),
                volume=d["exec_qty"],
                datetime=generate_datetime(d["trade_time"]),
                gateway_name=self.gateway_name,
            )

            self.gateway.on_trade(trade)

    def on_order(self, packet: dict) -> None:
        """委托更新推送"""
        for d in packet["data"]:
            orderid: str = d["order_link_id"]
            if orderid:
                local_orderids.add(orderid)
            else:
                orderid: str = d["order_id"]

            dt: datetime = generate_datetime(d["create_time"])

            order: OrderData = OrderData(
                symbol=d["symbol"],
                exchange=Exchange.BYBIT,
                orderid=orderid,
                type=ORDER_TYPE_BYBIT2VT[d["order_type"]],
                direction=DIRECTION_BYBIT2VT[d["side"]],
                price=float(d["price"]),
                volume=d["qty"],
                traded=d["cum_exec_qty"],
                status=STATUS_BYBIT2VT[d["order_status"]],
                datetime=dt,
                gateway_name=self.gateway_name
            )
            offset: bool = d["reduce_only"]
            if offset:
                order.offset = Offset.CLOSE
            else:
                order.offset = Offset.OPEN

            self.gateway.on_order(order)

    def on_position(self, packet: dict) -> None:
        """持仓更新推送"""
        for d in packet["data"]:
            position: PositionData = PositionData(
                symbol=d["symbol"],
                exchange=Exchange.BYBIT,
                direction=DIRECTION_BYBIT2VT[d["side"]],
                volume=d["size"],
                price=float(d["entry_price"]),
                gateway_name=self.gateway_name
            )
            self.gateway.on_position(position)


def generate_timestamp(expire_after: float = 30) -> int:
    """生成时间戳"""
    return int(time.time() * 1000 + expire_after * 1000)


def sign(secret: bytes, data: bytes) -> str:
    """生成签名"""
    return hmac.new(
        secret, data, digestmod=hashlib.sha256
    ).hexdigest()


def generate_datetime(timestamp: str) -> datetime:
    """生成时间"""
    if "." in timestamp:
        part1, part2 = timestamp.split(".")
        if len(part2) > 7:
            part2 = part2[:6] + "Z"
            timestamp = ".".join([part1, part2])

        dt: datetime = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%fZ")
    else:
        dt: datetime = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")

    dt = utc.localize(dt)
    return dt.astimezone(CHINA_TZ)


def generate_datetime_2(timestamp: int) -> datetime:
    """生成时间"""
    dt: datetime = datetime.fromtimestamp(timestamp)
    return CHINA_TZ.localize(dt)


def get_float_value(data: dict, key: str) -> float:
    """获取字典中对应键的浮点数值"""
    data_str: str = data.get(key, "")
    if not data_str:
        return 0.0
    return float(data_str)
