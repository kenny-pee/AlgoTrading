from AlgorithmImports import *
from Selection.FundamentalUniverseSelectionModel import FundamentalUniverseSelectionModel

from math import ceil
from itertools import chain

class GreenblattMagicFormulaAlpha(QCAlgorithm):
    def Initialize(self):

        self.SetStartDate(2018, 1, 1)
        self.SetCash(100000)
        self.UniverseSettings.Resolution = Resolution.Daily
        self._changes = None
        # select stocks using MagicFormulaUniverseSelectionModel
        self.SetUniverseSelection(GreenBlattMagicFormulaUniverseSelectionModel())

    def OnData(self, data):
        # if we have no changes, do nothing
        if self._changes is None: return

        # liquidate removed securities
        for security in self._changes.RemovedSecurities:
            if security.Invested:
                self.Debug("Sold on Universe Change: " + str(security.Symbol))
                self.Liquidate(security.Symbol)
                
        # we want 10% allocation in each security in our universe
        for security in self._changes.AddedSecurities:
            history = self.History(security.Symbol,10)
            if security.Price <= history.close.min():
                self.SetHoldings(security.Symbol, 0.1)
                # close = self.Securities[security.Symbol].Close
                # limitTicket = self.LimitOrder(security.Symbol, self.CalculateOrderQuantity(security.Symbol, 0.1), close * .98)
            
        self._changes = None

    def OnSecuritiesChanged(self, changes):
        self._changes = changes


class SelectionData(object):
    def __init__(self, symbol, period):
        self.symbol = symbol
        self.ema = SimpleMovingAverage(period)
        self.ma5 = SimpleMovingAverage(5)
        self.is_above_ema = False
        self.is_below_ma5 = False
        self.volume = 0

    def update(self, time, price, volume):
        self.volume = volume
        if self.ema.Update(time, price):
            self.is_above_ema = price > self.ema.Current.Value
        if self.ma5.Update(time,price):
            self.is_below_ma5 = price < self.ma5.Current.Value
            

class GreenBlattMagicFormulaUniverseSelectionModel(FundamentalUniverseSelectionModel):
    '''Defines a universe according to Joel Greenblatt's Magic Formula, as a universe selection model for the framework algorithm.
       From the universe QC500, stocks are ranked using the valuation ratios, Enterprise Value to EBITDA (EV/EBITDA) and Return on Assets (ROA).
    '''

    def __init__(self,
                 filterFineData = True,
                 universeSettings = None):
        '''Initializes a new default instance of the MagicFormulaUniverseSelectionModel'''
        super().__init__(filterFineData, universeSettings)

        # Number of stocks in Coarse Universe
        self.NumberOfSymbolsCoarse = 500
        # Number of sorted stocks in the fine selection subset using the valuation ratio, EV to EBITDA (EV/EBITDA)
        self.NumberOfSymbolsFine = 50
        # Final number of stocks in security list, after sorted by the valuation ratio, Return on Assets (ROA)
        self.NumberOfSymbolsInPortfolio = 10
        self.stateData = { }
        self.lastMonth = -1
        self.dollarVolumeBySymbol = {}

    def SelectCoarse(self, algorithm, coarse):
        '''Performs coarse selection for constituents.
        The stocks must have fundamental data'''
        month = algorithm.Time.day
        if month == self.lastMonth:
            return Universe.Unchanged
        self.lastMonth = month

        # sort the stocks by dollar volume and take the top 1000
        top = sorted([x for x in coarse if x.HasFundamentalData],
                    key=lambda x: x.DollarVolume, reverse=True)[:self.NumberOfSymbolsCoarse]
                    
        for c in top:
            if c.Symbol not in self.stateData:
                self.stateData[c.Symbol] = SelectionData(c.Symbol, 200)

            # Updates the SymbolData object with current EOD price
            avg = self.stateData[c.Symbol]
            avg.update(c.EndTime, c.AdjustedPrice, c.DollarVolume)

        # Filter the values of the dict to those above EMA and more than $1B vol.
        values = [x.symbol for x in self.stateData.values() if x.is_above_ema and x.is_below_ma5]
        
        # sort by the largest in volume.
        # values.sort(key=lambda x: x.volume, reverse=True)
        
        self.dollarVolumeBySymbol = { i : 1 for i in values }

        return list(self.dollarVolumeBySymbol.keys())


    def SelectFine(self, algorithm, fine):
        '''QC500: Performs fine selection for the coarse selection constituents
        The company's headquarter must in the U.S.
        The stock must be traded on either the NYSE or NASDAQ
        At least half a year since its initial public offering
        The stock's market cap must be greater than 500 million
        Magic Formula: Rank stocks by Enterprise Value to EBITDA (EV/EBITDA)
        Rank subset of previously ranked stocks (EV/EBITDA), using the valuation ratio Return on Assets (ROA)'''

        # QC500:
        ## The company's headquarter must in the U.S.
        ## The stock must be traded on either the NYSE or NASDAQ
        ## At least half a year since its initial public offering
        ## The stock's market cap must be greater than 500 million
        filteredFine = [x for x in fine if x.CompanyReference.CountryId == "USA"
                                        and (x.CompanyReference.PrimaryExchangeID == "NYS" or x.CompanyReference.PrimaryExchangeID == "NAS")
                                        and (algorithm.Time - x.SecurityReference.IPODate).days > 180
                                        and x.EarningReports.BasicAverageShares.ThreeMonths * x.EarningReports.BasicEPS.TwelveMonths * x.ValuationRatios.PERatio > 5e8]
        count = len(filteredFine)
        if count == 0: return []

        myDict = dict()
        percent = self.NumberOfSymbolsFine / count

        # select stocks with top dollar volume in every single sector
        for key in ["N", "M", "U", "T", "B", "I"]:
            value = [x for x in filteredFine if x.CompanyReference.IndustryTemplateCode == key]
            value = sorted(value, key=lambda x: self.dollarVolumeBySymbol[x.Symbol], reverse = True)
            myDict[key] = value[:ceil(len(value) * percent)]

        # stocks in QC500 universe
        topFine = chain.from_iterable(myDict.values())

        #  Magic Formula:
        ## Rank stocks by Enterprise Value to EBITDA (EV/EBITDA)
        ## Rank subset of previously ranked stocks (EV/EBITDA), using the valuation ratio Return on Assets (ROA)

        # sort stocks in the security universe of QC500 based on Enterprise Value to EBITDA valuation ratio
        sortedByEVToEBITDA = sorted(topFine, key=lambda x: x.ValuationRatios.EVToEBITDA , reverse=True)

        # sort subset of stocks that have been sorted by Enterprise Value to EBITDA, based on the valuation ratio Return on Assets (ROA)
        sortedByROA = sorted(sortedByEVToEBITDA[:self.NumberOfSymbolsFine], key=lambda x: x.ValuationRatios.ForwardROA, reverse=False)

        # retrieve list of securites in portfolio
        return [f.Symbol for f in sortedByROA[:self.NumberOfSymbolsInPortfolio]]
