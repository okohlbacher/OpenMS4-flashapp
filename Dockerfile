# This image consumes build products; OpenMS and Vue are built independently.
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
ARG PYTHON_IMAGE
WORKDIR /app
COPY . /app
RUN python -c 'import re,sys; assert re.search(r"@sha256:[0-9a-f]{64}$",sys.argv[1]), "PYTHON_IMAGE must be pinned by digest"' "$PYTHON_IMAGE" \
 && python experimental/verify_artifacts.py artifacts.lock.json artifacts --check-host
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
 && pip install --no-deps artifacts/*.whl \
 && pip check
RUN mkdir -p /opt/openms4 /workspaces \
 && python experimental/verify_artifacts.py artifacts.lock.json artifacts --extract /opt/openms4 \
 && python -c 'import json,pathlib; p=pathlib.Path("settings.json"); s=json.loads(p.read_text()); s["workspaces_dir"]="/workspaces"; p.write_text(json.dumps(s,indent=2))'
ENV PATH="/opt/openms4/bin:${PATH}" \
    OPENMS_TOOL_PREFIX_PATH="/opt/openms4" \
    LD_LIBRARY_PATH="/opt/openms4/lib"
EXPOSE 8501
CMD ["python", "-m", "streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
