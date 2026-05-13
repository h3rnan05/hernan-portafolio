"""Regression model layer.

Pure-function modules (regression, feature_select, prediction) take pandas
frames and return plain-Python dicts so they're trivially unit-testable. The
``data`` module wraps the SQLAlchemy I/O for end-to-end use.
"""

from app.modeling.feature_select import select_features_greedy
from app.modeling.prediction import predict_next_return, prediction_record
from app.modeling.regression import DiagnosticResult, fit_and_diagnose

__all__ = [
    "DiagnosticResult",
    "fit_and_diagnose",
    "predict_next_return",
    "prediction_record",
    "select_features_greedy",
]
