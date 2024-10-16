from datetime import datetime, timedelta
from structlog import get_logger


# Use the newspaper session for consistency

logger = get_logger(__name__)


class PolynomialDelayEstimator:
    """
    A polynomial delay estimator that estimates the delay between articles based on the time of day.

    `sklearn.linear_model.LinearRegression` is used to fit a polynomial regression model to the data. The model is then
    used to estimate the delay between articles based on the time of day.
    """

    def __init__(self, coefficients: list[float], intercept: float):
        """
        Initialize the polynomial delay estimator.

        :param coefficients: The coefficients of the polynomial regression model.
        :param intercept: The intercept of the polynomial regression - i.e. the value of the polynomial when x=0.
        """
        self.coefficients = coefficients
        self.intercept = intercept

    def estimate_delay(self, minutes_since_midnight: float | int) -> float:
        # Calculate the polynomial value
        minutes_since_midnight = int(minutes_since_midnight)
        estimated_delay = self.intercept
        power = 1  # Start from x^1
        for coeff in self.coefficients:
            estimated_delay += coeff * (minutes_since_midnight ** power)
            power += 1  # Increment the power

        return estimated_delay

    def __call__(self, article_time: datetime) -> timedelta:
        # Convert current time to minutes since midnight
        y = article_time.hour * 60 + article_time.minute

        # Use the polynomial function to estimate delay
        predicted_delay = self.polynomial_delay_estimation(y)

        return timedelta(minutes=predicted_delay)
