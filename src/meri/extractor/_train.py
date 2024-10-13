"""
Regression model to predict time differences between consecutive articles.

This module contains functions to prepare data and fit a polynomial regression model to predict time differences between
consecutive articles. This functionality is separated as it depends on sklearn, which is only installed with:
`poetry install --with analysis`.
"""

from datetime import datetime
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import PolynomialFeatures


def prepare_data(published_at: list[datetime]):
    """
    Prepare data for polynomial regression model.

    `minutes_since_midnight` is a feature that represents the time of day in minutes since midnight, from when the first
    article was published. This feature is used to capture the cyclic nature of time of day.

    `time_diff` is the target variable representing the time difference between consecutive articles.
    """

    # Convert datetime into DataFrame and sort it
    df = pd.DataFrame(published_at, columns=["published_at"], dtype='datetime64[ns, UTC]')
    df = df.sort_values('published_at')

    # Calculate time differences in minutes
    df['time_diff'] = df['published_at'].diff().dt.total_seconds() / 60
    df.dropna(subset=['time_diff'], inplace=True)  # Remove first row (NaN)

    # Convert published_at to minutes since midnight
    df['minutes_since_midnight'] = df['published_at'].dt.hour * 60 + df['published_at'].dt.minute

    # Maybe someday we can use cyclic features for time of day, but it makes saving the model harder and less portable
    # df['sin_time'] = np.sin(2 * np.pi * df['minutes_since_midnight'] / (24 * 60))
    # df['cos_time'] = np.cos(2 * np.pi * df['minutes_since_midnight'] / (24 * 60))

    return df

def fit_polynomial_model(df: pd.DataFrame, degree: int = 5) -> tuple[np.ndarray, float]:
    """
    Fit a polynomial regression model to the data.

    :param df: DataFrame containing the time differences and minutes since midnight
    :param degree: Degree of the polynomial features
    :return: Tuple of coefficients and intercept
    """
    # Extract features and target variable
    X = df['minutes_since_midnight'].values.reshape(-1, 1)
    y = df['time_diff']

    # Create polynomial features
    poly_features = PolynomialFeatures(degree=degree, include_bias=False)
    X_poly = poly_features.fit_transform(X)

    # Fit the polynomial regression model
    model = LinearRegression()
    model.fit(X_poly, y)

    # Extract coefficients and intercept
    coefficients = model.coef_
    intercept = model.intercept_

    return coefficients, intercept

