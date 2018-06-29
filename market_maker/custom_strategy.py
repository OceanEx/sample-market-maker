import os
import sys
import atexit
import signal
import collections
import requests
import numpy as np
from time import sleep
from datetime import datetime
from os.path import getmtime
from market_maker.market_maker import ExchangeInterface, logger, watched_files_mtimes
from market_maker.utils import log, constants, errors, math
from market_maker.settings import settings
from market_maker.public_client import GDAXPublicClient, BitfnexPublicClient

Bitmex2GADX = {
    "XBTUSD" : "BTC-USD"
}

Bitmex2Bitfnex = {
    "XBTUSD" : "btcusd"
}

def softmax(x, theta=-.5):
    """
    This function computes the softmax probability
    given a numpy array.
    """
    ps = np.exp(x * theta)
    ps /= np.sum(ps)
    return ps


COMPARE_FAIR_VALUE = False
BURST_THRESHOLD_PCT = 0.001
# the maximium number of position (for both short and long)
MAX_ABS_POSITION = 2000
# if position / MAX_ABS_POSITION exceed this threshold, we need to adjust the prices
SKEWNESS_CONDITION = 0.2
SKEWNESS_TICK = 20
# the number of tick we want always add to the price offset
BASE_TICK = 2
# the number of tick we increase of wrong action
BULL_BEAR_TICK = 20
# the number of tick we increase of wrong action
FAIR_VALUE_TICK = 60

def XBt_to_XBT(XBt):
    return float(XBt) / constants.XBt_TO_XBT

def cost(instrument, quantity, price):
    mult = instrument["multiplier"]
    P = mult * price if mult >= 0 else mult / price
    return abs(quantity * P)

def margin(instrument, quantity, price):
    return cost(instrument, quantity, price) * instrument["initMargin"]


class CustomOrderManager(object):
    """A sample order manager for implementing your own custom strategy"""
        
    def __init__(self):
        self.exchange = ExchangeInterface(settings.DRY_RUN)      
        # Once exchange is created, register exit handler that will always cancel orders
        # on any error.
        atexit.register(self.exit)
        signal.signal(signal.SIGTERM, self.exit)

        logger.info("Using symbol %s." % self.exchange.symbol)

        if settings.DRY_RUN:
            logger.info("Initializing dry run. Orders printed below represent what would be posted to BitMEX.")
        else:
            logger.info("Order Manager initializing, connecting to BitMEX. Live run: executing real trades.")

        self.start_time = datetime.now()
        self.instrument = self.exchange.get_instrument()
        self.starting_qty = self.exchange.get_delta()
        self.running_qty = self.starting_qty

        self.bear = False
        self.bull = False
        
        # initialize other exchange for fair value computation
        self.gdx_exchange = GDAXPublicClient()
        logger.info("Initializing Gdax public clinet.")
        self.bitfnex_exchange = BitfnexPublicClient()
        logger.info("Initializing Bitfnex public clinet.")
        
        # store prices and fair values
        self.prices = collections.deque([], maxlen=5)
        self.fair_values = collections.deque([], maxlen=5)
                        
        self.reset()        
        
    def reset(self):
        self.exchange.cancel_all_orders()
        self.sanity_check()
        self.print_status()
        
        
    def print_status(self):
        """Print the current MM status."""

        margin = self.exchange.get_margin()
        position = self.exchange.get_position()
        self.running_qty = self.exchange.get_delta()
        tickLog = self.exchange.get_instrument()['tickLog']
        self.start_XBt = margin["marginBalance"]

        logger.info("Current XBT Balance: %.6f" % XBt_to_XBT(self.start_XBt))
        logger.info("Current Contract Position: %d" % self.running_qty)
        if settings.CHECK_POSITION_LIMITS:
            logger.info("Position limits: %d/%d" % (settings.MIN_POSITION, settings.MAX_POSITION))
        if position['currentQty'] != 0:
            logger.info("Avg Cost Price: %.*f" % (tickLog, float(position['avgCostPrice'])))
            logger.info("Avg Entry Price: %.*f" % (tickLog, float(position['avgEntryPrice'])))
        logger.info("Contracts Traded This Run: %d" % (self.running_qty - self.starting_qty))
        logger.info("Total Contract Delta: %.4f XBT" % self.exchange.calc_delta()['spot'])
        
             
    #################### Sanity ####################    

    def sanity_check(self):
        """Perform checks before placing orders."""
        # Check if OB is empty - if so, can't quote.
        self.exchange.check_if_orderbook_empty()

        # Ensure market is still open.
        self.exchange.check_market_open()

        # Get ticker, which sets price offsets and prints some debugging info.
        ticker = self.get_ticker()

        # Sanity check:
        if self.get_price_offset(-1) >= ticker["sell"] or self.get_price_offset(1) <= ticker["buy"]:
            logger.error("Buy: %s, Sell: %s" % (self.start_position_buy, self.start_position_sell))
            logger.error("First buy position: %s\nBitMEX Best Ask: %s\nFirst sell position: %s\nBitMEX Best Bid: %s" %
                         (self.get_price_offset(-1), ticker["sell"], self.get_price_offset(1), ticker["buy"]))
            logger.error("Sanity check failed, exchange data is inconsistent")
            self.exit()

#         # Messaging if the position limits are reached
#         if self.long_position_limit_exceeded():
#             logger.info("Long delta limit exceeded")
#             logger.info("Current Position: %.f, Maximum Position: %.f" %
#                         (self.exchange.get_delta(), settings.MAX_POSITION))

#         if self.short_position_limit_exceeded():
#             logger.info("Short delta limit exceeded")
#             logger.info("Current Position: %.f, Minimum Position: %.f" %
#                         (self.exchange.get_delta(), settings.MIN_POSITION))        
            
    
    #################### info ####################            
    
    def get_ticker(self):
        ticker = self.exchange.get_ticker()
        tickLog = self.exchange.get_instrument()['tickLog']
        
        self.compute_start_position(ticker)
        
#         # Set up our buy & sell positions as the smallest possible unit above and below the current spread
#         # and we'll work out from there. That way we always have the best price but we don't kill wide
#         # and potentially profitable spreads.        
#         self.start_position_buy = ticker["buy"] + self.instrument['tickSize']
#         self.start_position_sell = ticker["sell"] - self.instrument['tickSize']

#         # If we're maintaining spreads and we already have orders in place,
#         # make sure they're not ours. If they are, we need to adjust, otherwise we'll
#         # just work the orders inward until they collide.
#         if settings.MAINTAIN_SPREADS:
#             if ticker['buy'] == self.exchange.get_highest_buy()['price']:
#                 self.start_position_buy = ticker["buy"]
#             if ticker['sell'] == self.exchange.get_lowest_sell()['price']:
#                 self.start_position_sell = ticker["sell"]

#         # Back off if our spread is too small.
#         if self.start_position_buy * (1.00 + settings.MIN_SPREAD) > self.start_position_sell:
#             self.start_position_buy *= (1.00 - (settings.MIN_SPREAD / 2))
#             self.start_position_sell *= (1.00 + (settings.MIN_SPREAD / 2))

#         # Midpoint, used for simpler order placement.
#         self.start_position_mid = ticker["mid"]
#         logger.info(
#             "%s Ticker: Buy: %.*f, Sell: %.*f" %
#             (self.instrument['symbol'], tickLog, ticker["buy"], tickLog, ticker["sell"])
#         )
#         logger.info('Start Positions: Buy: %.*f, Sell: %.*f, Mid: %.*f' %
#                     (tickLog, self.start_position_buy, tickLog, self.start_position_sell,
#                      tickLog, self.start_position_mid))
        
        return ticker
    
        
    def get_price_offset(self, index):
        """
        Given an index (1, -1, 2, -2, etc.) return the price for that side of the book.
        Negative is a buy, positive is a sell.
        """         
        start_position = self.start_position_buy if index < 0 else self.start_position_sell
        # First positions (index 1, -1) should start right at start_position, others should branch from there
        index = index + 1 if index < 0 else index - 1
        
        return math.toNearest(start_position * (1 + settings.INTERVAL) ** index, self.instrument['tickSize'])
            
        
    def compute_start_position(self, ticker):
        """
        This function calculates the start position for both buy and sell.
        """
        base_price = self.compute_base_price(ticker, "last")        

        if len(self.prices) == 0:
            self.prices.append(base_price)        
            
        # append the current base price into the historical prices            
        self.prices.append(base_price)
        
        # base price needs to be smoothed
        price_attention = softmax(np.array(range(len(self.prices) - 1, -1, -1)))
        base_price = np.sum(price_attention * self.prices)
               
        # This compute the differences between consective prices --> magnitude of price changing speed
        price_differences = np.abs(np.abs(np.array(self.prices)[1:] - np.array(self.prices)[:-1]))
        
        # spread is propotional to the magnitude of price changing speed
        # high speed --> larger spread, low speed --> small spread. 
        # this is talored by a exponetial decay
        speed_attention = softmax(np.array(range(len(price_differences) - 1, -1, -1)))             
        offset_price = np.sum(speed_attention * price_differences)
        offset_price += BASE_TICK * self.instrument['tickSize']
                      
        self.start_position_buy = base_price - offset_price
        self.start_position_sell = base_price + offset_price
                    
        # this price is used to determine a "bear" or "bull" event.
        burst_price = self.prices[-1] * BURST_THRESHOLD_PCT
        self.check_bear_bull(burst_price)
                
        if self.bull == True:
            # Going up quickly. Adjust the sell price --> higher
            # Adjust buy price --> to have more long position. 
            self.start_position_buy += (BASE_TICK / 2) * self.instrument['tickSize']
            self.start_position_sell += BULL_BEAR_TICK * self.instrument['tickSize']
        
        if self.bear == True:
            # Going down quickly.  Adjust the buy price.
            self.start_position_buy -= BULL_BEAR_TICK * self.instrument['tickSize']
            self.start_position_sell -= (BASE_TICK / 2) * self.instrument['tickSize']
                    
        # check my own inventory
        position =  self.exchange.get_delta()
        
        if (position / MAX_ABS_POSITION) > SKEWNESS_CONDITION:            
            # we have a lot of long position
            # Need to reduce the buy price, and maybe lower the sell price
            self.start_position_buy -= abs(position / MAX_ABS_POSITION) * SKEWNESS_TICK * self.instrument['tickSize']         
 
        if (position / MAX_ABS_POSITION) < -SKEWNESS_CONDITION:
            # we have a lot of short position
            # Need to raise the sell price, maybe raise buy price      
            self.start_position_sell += abs(position / MAX_ABS_POSITION) * SKEWNESS_TICK * self.instrument['tickSize']         
          
        # check for maintain the spread.  
        # buy price cannot higher or equal to the best asks        
        while math.toNearest(self.start_position_buy, self.instrument['tickSize']) >= ticker["sell"]:
            # my buy position exceed the sell price
            # no longer a limited order anymore. --> back off       
            self.start_position_buy -= self.instrument['tickSize']
        
        while math.toNearest(self.start_position_sell, self.instrument['tickSize']) <= ticker["buy"]:
            # the sell price is lower the buy price
            # no longer a limited order anymore. --> back off
            self.start_position_sell += self.instrument['tickSize']
                        
        # compare the price with fair value
        if COMPARE_FAIR_VALUE:
            fair_value = self.compute_fair_value()
            self.fair_values.append(fair_value)
            fv_attention = softmax(np.array(range(len(self.fair_values) - 1, -1, -1)))
            fair_value = np.sum(fv_attention * self.fair_values)
            logger.info("Fair Value: %.f" % (fair_value))                        
            
            # buy price cannot higher than f.v. by a threshold 
            while self.start_position_buy - fair_value > FAIR_VALUE_TICK * self.instrument['tickSize']:
                self.start_position_buy -= self.instrument['tickSize']                        
            
            # sell price 
            while fair_value - self.start_position_sell > FAIR_VALUE_TICK * self.instrument['tickSize']:
                self.start_position_sell += self.instrument['tickSize']                  
        
    def check_bear_bull(self, burst_price):
        """
        This function is used to check if market if going to bull or bear.
        """
        self.bear = False
        self.bull = False
        
        prices_ = np.array(self.prices)
        bull_condition1 = prices_[-1] - np.max(prices_[:-1]) > burst_price
        if len(prices_) > 2:
            bull_condition2 = (prices_[-1] - np.max(prices_[:-2]) > burst_price) and (prices_[-1] > prices_[-2])
        else:
            bull_condition2 = False

        bear_condition1 = prices_[-1] - np.min(prices_[:-1]) < -burst_price
        if len(prices_) > 2:        
            bear_condition2 = (prices_[-1] - np.min(prices_[:-2]) < -burst_price) and (prices_[-1] < prices_[-2])
        else:
            bear_condition2 = False
        
        # check for bull
        if bull_condition1 or bull_condition2:
            self.bull = True
            
        if bear_condition1 or bear_condition2:
            self.bear = True
         
        if self.bull == True and self.bear == True:
            raise ValueError("bull and bear cannot be true at the same time.")
                    
                               
    def compute_base_price(self, ticker, method="last"):
        """
        compute the base price of market making. 
        """
        method = method if method in ["last", "mid"] else "last"

        if method == "last":
            base_price = ticker["last"]
        else:
            base_price = ticker["mid"]
            
        return base_price
        
    
    def compute_inventory_difference(self):
        """
        TODO: Compute the differnce between buy orders and sell orders 
        as a indicator for bull or beer.     
        """
        pass
    
   
    def compute_fair_value(self):
        """
        This function use the mean of the last price of 
        GDAX and Bitfnex as the fair value.  
        """
        gdax_symbol = Bitmex2GADX[self.exchange.symbol]
        bitfnex_symbol = Bitmex2Bitfnex[self.exchange.symbol]
    
        gdax_ticker = self.gdx_exchange.get_product_ticker(gdax_symbol)
        bitfnex_ticker = self.bitfnex_exchange.get_product_ticker(bitfnex_symbol)    
        gdax_last_price = float(gdax_ticker["price"])
        bitfnex_last_price = float(bitfnex_ticker["last_price"])
        fair_value = (gdax_last_price + bitfnex_last_price) / 2.0
        
        return fair_value
       

    #################### order ####################    
#     def place_orders(self):
#         # implement your custom strategy here

#         buy_orders = []
#         sell_orders = []

#         # populate buy and sell orders, e.g.
#         buy_price, self_price = self.get_price()
#         buy_orders.append({'price': buy_price, 'orderQty': settings.ORDER_START_SIZE, 'side': "Buy"})
#         sell_orders.append({'price': self_price, 'orderQty': settings.ORDER_START_SIZE, 'side': "Sell"})
#         self.converge_orders(buy_orders, sell_orders)
        
#     def get_price(self, min_tick=2):
#         """This function used to calculate the bidding price"""
#         if self.previous_mid == None:
#             self.previous_mid = self.start_position_mid
#         offset = (abs(self.start_position_mid - self.previous_mid) + min_tick) * self.instrument['tickSize']
#         buy_price = self.start_position_mid - offset
#         sell_price = self.start_position_mid + offset
#         buy_price = math.toNearest(buy_price, self.instrument['tickSize'])
#         sell_price = math.toNearest(sell_price, self.instrument['tickSize'])        
        
#         return buy_price, sell_price

    
    def place_orders(self):
        """Create order items for use in convergence."""
        buy_orders = []
        sell_orders = []
        # Create orders from the outside in. This is intentional - let's say the inner order gets taken;
        # then we match orders from the outside in, ensuring the fewest number of orders are amended and only
        # a new order is created in the inside. If we did it inside-out, all orders would be amended
        # down and a new order would be created at the outside.
        for i in reversed(range(1, settings.ORDER_PAIRS + 1)):
            if not self.long_position_limit_exceeded():
                buy_orders.append(self.prepare_order(-i))
            if not self.short_position_limit_exceeded():
                sell_orders.append(self.prepare_order(i))
        return self.converge_orders(buy_orders, sell_orders)


    def prepare_order(self, index):
        """
        Create an order object.
        """
        if settings.RANDOM_ORDER_SIZE is True:
            quantity = random.randint(settings.MIN_ORDER_SIZE, settings.MAX_ORDER_SIZE)
        else:
            quantity = settings.ORDER_START_SIZE + ((abs(index) - 1) * settings.ORDER_STEP_SIZE)

        price = self.get_price_offset(index)

        return {'price': price, 'orderQty': quantity, 'side': "Buy" if index < 0 else "Sell"}
    
    
    def converge_orders(self, buy_orders, sell_orders):
        """
        Converge the orders we currently have in the book with what we want to be in the book.
        This involves amending any open orders and creating new ones if any have filled completely.
        Start from the closest orders outward.
        """

        tickLog = self.exchange.get_instrument()['tickLog']
        to_amend = []
        to_create = []
        to_cancel = []
        buys_matched = 0
        sells_matched = 0
        existing_orders = self.exchange.get_orders()

        # Check all existing orders and match them up with what we want to place.
        # If there's an open one, we might be able to amend it to fit what we want.
        for order in existing_orders:
            try:
                if order['side'] == 'Buy':
                    desired_order = buy_orders[buys_matched]
                    buys_matched += 1
                else:
                    desired_order = sell_orders[sells_matched]
                    sells_matched += 1

                # Notes: orderQty is the total size of the order, 
                # cumQty is what's executed, and leavesQty is how much is still open. 
                # For example:
                # Order is placed for 1000: orderQty = 1000, cumQty = 0, leavesQty = 1000
                # 500 executes: orderQty = 1000, cumQty = 500, leavesQty = 500
                # Order is amended to leavesQty: 1000: orderQty = 1500, cumQty = 500, leavesQty = 1000
                # Order is amended to orderQty: 1200: orderQty = 1200, cumQty = 500, leavesQty = 700.
                    
                # Found an existing order. Do we need to amend it?
                if desired_order['orderQty'] != order['leavesQty'] or (
                        # If price has changed, and the change is more than our RELIST_INTERVAL, amend.
                        desired_order['price'] != order['price'] and
                        abs((desired_order['price'] / order['price']) - 1) > settings.RELIST_INTERVAL):
                    to_amend.append({'orderID': order['orderID'], 'orderQty': order['cumQty'] + desired_order['orderQty'],
                                     'price': desired_order['price'], 'side': order['side']})
            except IndexError:
                # Will throw if there isn't a desired order to match. In that case, cancel it.
                to_cancel.append(order)

        while buys_matched < len(buy_orders):
            # the exisitng buy orders are less the desired buy orders
            to_create.append(buy_orders[buys_matched])
            buys_matched += 1

        while sells_matched < len(sell_orders):
            # the exisitng sell orders are less the desired sell orders            
            to_create.append(sell_orders[sells_matched])
            sells_matched += 1
        
        # Reorder         
        if len(to_amend) > 0:
            for amended_order in reversed(to_amend):
                reference_order = [o for o in existing_orders if o['orderID'] == amended_order['orderID']][0]
                logger.info("Amending %4s: %d @ %.*f to %d @ %.*f (%+.*f)" % (
                    amended_order['side'],
                    reference_order['leavesQty'], tickLog, reference_order['price'],
                    (amended_order['orderQty'] - reference_order['cumQty']), tickLog, amended_order['price'],
                    tickLog, (amended_order['price'] - reference_order['price'])
                ))
            # This can fail if an order has closed in the time we were processing.
            # The API will send us `invalid ordStatus`, which means that the order's status (Filled/Canceled)
            # made it not amendable.
            # If that happens, we need to catch it and re-tick.
            try:
                self.exchange.amend_bulk_orders(to_amend)
            except requests.exceptions.HTTPError as e:
                errorObj = e.response.json()
                if errorObj['error']['message'] == 'Invalid ordStatus':
                    logger.warn("Amending failed. Waiting for order data to converge and retrying.")
                    sleep(0.5)
                    return self.place_orders()
                else:
                    logger.error("Unknown error on amend: %s. Exiting" % errorObj)
                    sys.exit(1)

        if len(to_create) > 0:
            logger.info("Creating %d orders:" % (len(to_create)))
            for order in reversed(to_create):
                logger.info("%4s %d @ %.*f" % (order['side'], order['orderQty'], tickLog, order['price']))
            self.exchange.create_bulk_orders(to_create)

        # Could happen if we exceed a delta limit
        if len(to_cancel) > 0:
            logger.info("Canceling %d orders:" % (len(to_cancel)))
            for order in reversed(to_cancel):
                logger.info("%4s %d @ %.*f" % (order['side'], order['leavesQty'], tickLog, order['price']))
            self.exchange.cancel_bulk_orders(to_cancel)
    
    
    
    #################### position limits ####################    
    def short_position_limit_exceeded(self):
        """
        Returns True if the short position limit is exceeded.
        """
        position = self.exchange.get_delta()
        return position <= -MAX_ABS_POSITION

    def long_position_limit_exceeded(self):
        """
        Returns True if the long position limit is exceeded.
        """
        position = self.exchange.get_delta()
        return position >= MAX_ABS_POSITION 

        
        

    #################### running ####################    


    def check_file_change(self):
        """Restart if any files we're watching have changed."""
        for f, mtime in watched_files_mtimes:
            if getmtime(f) > mtime:
                self.restart()

    def check_connection(self):
        """Ensure the WS connections are still open."""
        return self.exchange.is_open()
        
    def exit(self):
        logger.info("Shutting down. All open orders will be cancelled.")
        try:
            self.exchange.cancel_all_orders()
            self.exchange.bitmex.exit()
        except errors.AuthenticationError as e:
            logger.info("Was not authenticated; could not cancel orders.")
        except Exception as e:
            logger.info("Unable to cancel orders: %s" % e)
        sys.exit()

    def run_loop(self):
        while True:
            sys.stdout.write("-----\n")
            sys.stdout.flush()

            self.check_file_change()
            sleep(settings.LOOP_INTERVAL)

            # This will restart on very short downtime, but if it's longer,
            # the MM will crash entirely as it is unable to connect to the WS on boot.
            if not self.check_connection():
                logger.error("Realtime data connection unexpectedly closed, restarting.")
                self.restart()

            self.sanity_check()  # Ensures health of mm - several cut-out points here
            self.print_status()  # Print skew, delta, etc
            self.place_orders()  # Creates desired orders and converges to existing orders

    def restart(self):
        logger.info("Restarting the market maker...")
        os.execv(sys.executable, [sys.executable] + sys.argv)        

    
def run():
    order_manager = CustomOrderManager()
    # Try/except just keeps ctrl-c from printing an ugly stacktrace
    try:
        order_manager.run_loop()
    except (KeyboardInterrupt, SystemExit):
        sys.exit()
