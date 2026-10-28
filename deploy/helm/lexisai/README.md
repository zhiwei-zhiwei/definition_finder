# LexisAI Helm chart

Backend (FastAPI) + frontend (Next.js). Environments: `values-kind.yaml` (Mac, kind).
`values-jetson.yaml` arrives with LX-2, `values-eks.yaml` with LX-3.

## Install on kind

    kind create cluster --config deploy/kind/cluster.yaml
    docker build -t lexisai-backend:dev backend && docker build -t lexisai-frontend:dev frontend
    kind load docker-image lexisai-backend:dev lexisai-frontend:dev --name lexisai
    kubectl create secret generic lexisai-secrets \
      --from-literal=OPENAI_API_KEY="$(grep '^OPENAI_API_KEY=' backend/.env | cut -d= -f2-)" \
      --from-literal=JWT_SECRET="$(openssl rand -hex 32)"
    helm repo add traefik https://traefik.github.io/charts
    helm install traefik traefik/traefik --version 42.1.3 -n traefik --create-namespace -f deploy/kind/traefik-values.yaml
    # build the frontend with an empty API base: it then calls the API on its own origin (the Ingress)
    docker build -t lexisai-frontend:dev --build-arg NEXT_PUBLIC_API_BASE= frontend
    helm install lexisai deploy/helm/lexisai -f deploy/helm/lexisai/values-kind.yaml
    open http://localhost

After rebuilding an image with the same tag: `kind load ...` then `kubectl rollout restart deploy/lexisai-backend`.

## Why the backend runs exactly one replica

- **SQLite allows one writer.** `app.db` and `checkpoints.db` live on the backend's disk; two
  processes writing through a shared volume risk lock errors and corruption.
- **The disk is ReadWriteOnce.** The PVC holding the DBs, uploads and the Docling model cache can be
  mounted by one node at a time, so a second replica could not even start elsewhere.
- **Hence `strategy: Recreate`.** The default rolling update starts the new Pod before stopping the
  old one: two Pods on one SQLite file for a moment. Recreate accepts a few seconds of downtime instead.

Scaling out is LX-6 (Postgres + pgvector). Until then the stateless part that scales is the reranker (LX-2).

## Resources

|          | requests       | limit             | why                                               |
| -------- | -------------- | ----------------- | ------------------------------------------------- |
| backend  | 500m CPU, 1Gi  | 4Gi (kind: 2.5Gi) | idle ~210 MB; Docling prepare peaks at 3.1-3.3 GB |
| frontend | 50m CPU, 128Mi | 256Mi             | ~50 MB idle                                       |

No CPU limits: Docling uses every core it gets, and a CPU limit only throttles it.
The kind limit is lower because Docker Desktop gives the node ~3.8 GiB: with 4 GB of Docker
memory, PDFs that need more than 2.5 GB of non-reclaimable memory (the AMD 10-K) fail with
OOMKilled. The 51-page lease fits: it touches the limit, but the kernel reclaims page cache first.

## CI

`.github/workflows/ci.yml` → `k8s-smoke` repeats the kind install above on every push: build both
images, kind + Traefik + this chart (`helm install --wait`), then `/health`, `/` and an upload
through the Ingress. On `main` it also asks one question and fails unless the SSE stream reaches
`done` without an `error` (needs the `OPENAI_API_KEY` repo secret).
