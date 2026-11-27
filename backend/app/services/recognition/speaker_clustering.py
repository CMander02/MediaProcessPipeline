"""Keep established voices when a known count removes small extra clusters."""

import math
import numpy as np


def absorb_micro_clusters(
    embeddings,
    labels,
    *,
    min_ratio: float = 0.015,
    min_count: int = 3,
    min_cosine_similarity: float = 0.55,
    min_clusters: int | None = None,
):
    """Absorb tiny outlier clusters into the nearest major cluster when acoustically close."""
    labels = np.asarray(labels).copy()
    if len(embeddings) < 20:
        return labels
    unique, sizes = np.unique(labels, return_counts=True)
    if len(unique) < 2:
        return labels
    floor = max(min_count, math.ceil(len(embeddings) * min_ratio))
    major = [label for label, size in zip(unique, sizes) if size >= floor]
    if not major:
        return labels
    vectors = np.asarray(embeddings, dtype=np.float64)
    vectors = vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
    major_centers = np.stack([vectors[labels == label].mean(axis=0) for label in major])
    major_centers /= np.maximum(np.linalg.norm(major_centers, axis=1, keepdims=True), 1e-12)
    min_keep = max(1, int(min_clusters or 1))
    current_unique = len(unique)
    small = sorted(
        [(size, label) for label, size in zip(unique, sizes) if size < floor],
        key=lambda item: item[0],
    )
    for _, label in small:
        if current_unique <= min_keep:
            break
        center = vectors[labels == label].mean(axis=0)
        norm = np.linalg.norm(center)
        if norm <= 1e-12:
            continue
        center = center / norm
        sims = major_centers @ center
        best_idx = int(np.argmax(sims))
        if float(sims[best_idx]) >= min_cosine_similarity:
            labels[labels == label] = major[best_idx]
            current_unique -= 1
    return labels


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
                return labels
            if max_clusters is not None and max_clusters > 0:
                labels = super().cluster(embeddings.copy(), 1, len(embeddings))
                labels = absorb_micro_clusters(embeddings, labels, min_clusters=min_clusters)
                if len(np.unique(labels)) > max_clusters:
                    return merge_to_known_count(embeddings, labels, max_clusters, strategy=strategy)
                if min_clusters is not None and len(np.unique(labels)) < min_clusters:
                    return super().cluster(embeddings, min_clusters, max_clusters, num_clusters)
                return labels
            labels = super().cluster(embeddings, min_clusters, max_clusters, num_clusters)
            return absorb_micro_clusters(embeddings, labels, min_clusters=min_clusters)

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
