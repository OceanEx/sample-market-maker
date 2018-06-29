# Reference:
# https://github.com/danpaquin/gdax-python/blob/master/gdax/public_client.py#L7
# https://docs.bitfinex.com/v1/reference#rest-public-ticker
import requests

class PublicClient(object):
    def __init__(self, api_url, timeout):
        self.url = api_url.rstrip('/')
        self.timeout = timeout
        
    def _get(self, path, params=None):
        """Perform get request"""
        r = requests.get(self.url + path, params=params, timeout=self.timeout)
        return r.json()
    
    def get_products(self):
        pass
    
    def get_product_order_book(self, product_id, level):
        pass

    def get_product_ticker(self, product_id):
        pass    
    

class GDAXPublicClient(PublicClient):
    def __init__(self, api_url='https://api.gdax.com', timeout=30):
        """
        Create GDAX API public client. 
        """
        super(GDAXPublicClient, self).__init__(api_url, timeout)
        
    
    def get_products(self):
        """Get a list of available currency pairs for trading.
        Returns:
            list: Info about all currency pairs. Example::
                [
                    {
                        "id": "BTC-USD",
                        "display_name": "BTC/USD",
                        "base_currency": "BTC",
                        "quote_currency": "USD",
                        "base_min_size": "0.01",
                        "base_max_size": "10000.00",
                        "quote_increment": "0.01"
                    }
                ]
        """
        return self._get('/products')
    
    def get_product_order_book(self, product_id, level=1):
        """Get a list of open orders for a product.
        The amount of detail shown can be customized with the `level`
        parameter:
        * 1: Only the best bid and ask
        * 2: Top 50 bids and asks (aggregated)
        * 3: Full order book (non aggregated)
        Level 1 and Level 2 are recommended for polling. For the most
        up-to-date data, consider using the websocket stream.
        **Caution**: Level 3 is only recommended for users wishing to
        maintain a full real-time order book using the websocket
        stream. Abuse of Level 3 via polling will cause your access to
        be limited or blocked.
        Args:
            product_id (str): Product
            level (Optional[int]): Order book level (1, 2, or 3).
                Default is 1.
        Returns:
            dict: Order book. Example for level 1::
                {
                    "sequence": "3",
                    "bids": [
                        [ price, size, num-orders ],
                    ],
                    "asks": [
                        [ price, size, num-orders ],
                    ]
                }
        """
        # Supported levels are 1, 2 or 3
        level = level if level in range(1, 4) else 1
        return self._get('/products/{}/book'.format(str(product_id)), params={'level': level})
    
    def get_product_ticker(self, product_id):
        """Snapshot about the last trade (tick), best bid/ask and 24h volume.
        **Caution**: Polling is discouraged in favor of connecting via
        the websocket stream and listening for match messages.
        Args:
            product_id (str): Product
        Returns:
            dict: Ticker info. Example::
                {
                  "trade_id": 4729088,
                  "price": "333.99",
                  "size": "0.193",
                  "bid": "333.98",
                  "ask": "333.99",
                  "volume": "5957.11914015",
                  "time": "2015-11-14T20:46:03.511254Z"
                }
        """
        return self._get('/products/{}/ticker'.format(str(product_id)))    


class BitfnexPublicClient(PublicClient):
    def __init__(self, api_url='https://api.bitfinex.com/v1', timeout=30):
        """
        Create Bitfnex API public client. 
        """
        super(BitfnexPublicClient, self).__init__(api_url, timeout)
        
    def get_products(self):
        """Get a list of available currency pairs for trading.
        Returns:
            list: Info about all currency pairs. Example::
                [
                    {
                        "pair":"btcusd",
                        "price_precision":5,
                        "initial_margin":"30.0",
                        "minimum_margin":"15.0",
                        "maximum_order_size":"2000.0",
                        "minimum_order_size":"0.002",
                        "expiration":"NA",
                        "margin":true
                    }
                ]
        """
        return self._get('/symbols_details')
    
    def get_product_order_book(self, product_id, level=None):
        """Get a list of open orders for a product.
        The `level` is a dummy parameter:
        Args:
            product_id (str): Product
            level: No use.  
        Returns:
            dict: Order book. 
                {
                    "bids": [
                        {'amount': '1.32329785', 'price': '6168.6', 'timestamp': '1529868666.0'},
                    ],
                    "asks": [
                        {'amount': '0.088', 'price': '6167.2', 'timestamp': '1529868666.0'},
                    ]
                }
            It contains 25 data for both asks and bids side.  
        """        
        return self._get('/book/{}'.format(str(product_id)))

    def get_product_ticker(self, product_id):
        """Snapshot about the last trade (tick), best bid/ask and 24h volume.
        Args:
            product_id (str): Product
        Returns:
            dict: Ticker info. Example::
            {
                'ask': '6174.1',
                'bid': '6173.9',
                'high': '6259.0',
                'last_price': '6174.0',
                'low': '5755.0',
                'mid': '6174.0',
                'timestamp': '1529869192.9802985',
                'volume': '45739.818033700016'
             }
        """
        return self._get('/pubticker/{}'.format(str(product_id)))

