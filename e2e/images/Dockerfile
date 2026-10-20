# Supply a reviewed direct linux/amd64 manifest, never a tag or guessed digest.
ARG PYTHON_BASE
FROM ${PYTHON_BASE}
ARG VERSION=v1
ARG MODE=http
ARG USER_ID=10001
ENV FIXTURE_VERSION=${VERSION} FIXTURE_MODE=${MODE} PYTHONDONTWRITEBYTECODE=1
WORKDIR /fixture
COPY app.py /fixture/app.py
USER ${USER_ID}:${USER_ID}
HEALTHCHECK --interval=1s --timeout=1s --start-period=1s --retries=2 CMD ["python3", "/fixture/app.py", "healthcheck"]
ENTRYPOINT ["python3", "/fixture/app.py"]
