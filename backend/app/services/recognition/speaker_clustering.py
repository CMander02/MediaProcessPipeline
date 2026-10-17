"""Keep established voices when a known count removes small extra clusters."""

import numpy as np


def merge_to_known_count(embeddings, labels, count, *, strategy="largest"):
    """Seed normalized k-means with the largest acoustic clusters.

    Cutting the dendrogram at N can merge the main voices while preserving an
    outlier. Established clusters provide better starting centers for this case.
    """
    from sklearn.cluster import KMeans

    vectors = np.asarray(embeddings, dtype=np.float64).copy()
    vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
    unique, sizes = np.unique(labels, return_counts=True)
    if strategy == "kmeans++":
        return KMeans(n_clusters=count, init="k-means++", n_init=10, random_state=0).fit_predict(vectors)
    seeds = unique[np.argsort(-sizes, kind="stable")[:count]]
    if strategy == "diverse":
        candidates = np.stack([vectors[labels == label].mean(axis=0) for label in unique])
        candidates /= np.maximum(np.linalg.norm(candidates, axis=1, keepdims=True), 1e-12)
        chosen = [int(np.argmax(sizes))]
        while len(chosen) < count:
            distances = 1 - candidates @ candidates[chosen].T
            separation = distances.min(axis=1)
            separation[chosen] = -np.inf
            chosen.append(int(np.argmax(separation)))
        seeds = unique[chosen]
    elif strategy != "largest":
        raise ValueError(f"Unknown clustering strategy: {strategy}")
    centers = np.stack([vectors[labels == label].mean(axis=0) for label in seeds])
    centers /= np.maximum(np.linalg.norm(centers, axis=1, keepdims=True), 1e-12)
    return KMeans(n_clusters=count, init=centers, n_init=1, random_state=0).fit_predict(vectors)


def with_known_count_clustering(previous, *, strategy="largest"):
    # Load pyannote only when the acoustic pipeline is requested.
    from pyannote.audio.pipelines.clustering import AgglomerativeClustering

    if strategy == "native" or not isinstance(previous, AgglomerativeClustering):
        return previous

    class KnownCountClustering(AgglomerativeClustering):
        def cluster(self, embeddings, min_clusters, max_clusters, num_clusters=None):
            count = num_clusters or (min_clusters if min_clusters == max_clusters else None)
            if count is not None:
                labels = super().cluster(embeddings.copy(), 1, len(embeddings))
                if len(np.unique(labels)) > count:
                    return merge_to_known_count(embeddings, labels, count, strategy=strategy)
            return super().cluster(embeddings, min_clusters, max_clusters, num_clusters)

    return KnownCountClustering(
        metric=previous.metric,
        max_num_embeddings=previous.max_num_embeddings,
        constrained_assignment=previous.constrained_assignment,
    ).instantiate(previous.parameters(instantiated=True))


def assignment_confidence(embeddings, labels):
    """Return cosine distances and margins for a finite calibration sweep."""
    vectors = np.asarray(embeddings, dtype=np.float64).copy()
    vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
    labels = np.asarray(labels)
    unique = np.unique(labels)
    centers = np.stack([vectors[labels == label].mean(axis=0) for label in unique])
    centers /= np.maximum(np.linalg.norm(centers, axis=1, keepdims=True), 1e-12)
    distances = np.clip(1 - vectors @ centers.T, 0, 2)
    ordered = np.sort(distances, axis=1)
    margin = ordered[:, 1] - ordered[:, 0] if len(unique) > 1 else np.full(len(vectors), np.inf)
    return {"nearest": unique[np.argmin(distances, axis=1)], "distance": ordered[:, 0], "margin": margin}
