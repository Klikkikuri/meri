from datetime import datetime, timedelta
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from structlog import get_logger
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException
from urllib3 import Retry

from meri.settings import settings

logger = get_logger(__name__)

class NotAllowedByRobotsTxt(RequestException):
    """
    Raised when the robots.txt file denies access to the URL.
    """
    pass


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


class RobotsHTTPAdapter(HTTPAdapter):
    """
    Implements the `requests.adapters.HTTPAdapter` to handle the `robots.txt` file.
    """
    def __init__(self, *args, **kwargs):
        self.robots_txt: dict[str, RobotFileParser] = {}
        self.bot_id = settings.BOT_ID

        # Setup retries
        retries = Retry(
            total=5,
        )
        kwargs.setdefault("max_retries", retries)
        return super().__init__(*args, **kwargs)
    
    def send(self, request, **kwargs):
        # Check if the request is for the robots.txt file
        url_parts = urlparse(request.url)
        if url_parts.path != "/robots.txt":
            # Check if the URL is allowed by robots.txt
            if not self._check_robot_access(request):
                raise NotAllowedByRobotsTxt(f"URL {request.url} is not allowed by robots.txt")

        return super().send(request, **kwargs)


    def _check_robot_access(self, request) -> bool:
        url_parts = urlparse(request.url)
        base_url = f"{url_parts.scheme}://{url_parts.netloc}"
        if base_url not in self.robots_txt:
            self.robots_txt[base_url] = RobotFileParser()
            robots_url = f"{base_url}/robots.txt"

            robots_file_request = request.copy()
            robots_file_request.method = "GET"
            robots_file_request.url = robots_url
            try:
                robots_response = self.send(robots_file_request, stream=False)
                if robots_response.ok:
                    logger.debug("Fetched robots.txt", base_url=base_url, status=robots_response.status_code, text=robots_response.text)
                    self.robots_txt[base_url].parse(robots_response.text.splitlines())
                else:
                    # Allow all if the robots.txt file is not found
                    self.robots_txt[base_url].allow_all = True
            except Exception as e:
                logger.error("Failed to fetch robots.txt", base_url=base_url, error=e, exc_info=True)
                return True

        can_access = self.robots_txt[base_url].can_fetch(self.bot_id, request.url)
        if not can_access:
            logger.warning("URL %r not allowed by for robot %r", request.url, self.bot_id, extra={"bot_id": self.bot_id, "robots.txt": self.robots_txt[base_url].entries})
        return can_access
