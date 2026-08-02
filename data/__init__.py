"""
数据模块 - 提供股票数据获取和处理功能
"""
#from .akshare_client import AKShareClien
from .akshare_client_fixed import AKShareClient
from .indicators import TechnicalIndicators

__all__ = ["AKShareClient", "TechnicalIndicators"]
