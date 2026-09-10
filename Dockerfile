# Consume verified runtime products; never rebuild OpenMS in this app.
# Pass an immutable Python image, e.g. python:3.12-slim@sha256:<verified digest>.
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
WORKDIR /app
COPY . /app
RUN python experimental/verify_artifacts.py artifacts.lock.json artifacts
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-deps artifacts/*.whl
RUN mkdir -p /opt/openms4 && python experimental/verify_artifacts.py artifacts.lock.json artifacts --extract /opt/openms4
ENV PATH="/opt/openms4/bin:${PATH}" \
    OPENMS_TOOL_PREFIX_PATH="/opt/openms4" \
    LD_LIBRARY_PATH="/opt/openms4/lib"
EXPOSE 8501
CMD ["python", "-m", "streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
