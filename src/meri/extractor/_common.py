from datetime import datetime, timedelta


class PolynomialDelayEstimator:
    """
    `sklearn.linear_model.LinearRegression` is used to fit a polynomial regression model to the data. The model is then
    used to estimate the delay between articles based on the time of day.
    """

    def __init__(self, coefficients: list[float], intercept: float):
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
