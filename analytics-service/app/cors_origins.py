import os


def cors_origins(env=None):
    env = os.environ if env is None else env
    configured = [origin.strip() for origin in env.get("CORS_ORIGINS", "").split(",") if origin.strip()]
    if env.get("NODE_ENV") == "production":
        return configured
    local = [f"http://{host}:{port}" for host in ("localhost", "127.0.0.1", "[::1]")
             for port in (4173, 5173, 5174, 5175, 5176, 5177, 5178, 5179)]
    return list(dict.fromkeys(configured + local))
