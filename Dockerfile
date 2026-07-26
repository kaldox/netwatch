# NetWatch – container image
# Runs the monitoring loop + dashboard. Use with the provided
# docker-compose.yml (host networking so it sees the real gateway/FritzBox).
FROM python:3.13-slim

# System tools the monitor shells out to: ping, traceroute, mtr, "ip route".
# tzdata so local time (generate_time, log timestamps) is correct.
RUN apt-get update && apt-get install -y --no-install-recommends \
        iputils-ping \
        traceroute \
        mtr-tiny \
        iproute2 \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY dashboard ./dashboard

# Runtime dirs (normally provided as bind-mount volumes; created here so the
# app also runs without mounts).
RUN mkdir -p config database data logs reports

# Dashboard port (published via host networking)
EXPOSE 8080

CMD ["python", "-m", "src.main"]
