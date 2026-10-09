"""Ponto de entrada para progresso; compartilha a transação das reservas."""
from .movie_draw import mutate


def set_progress(scope, movie_id, data):
    return mutate(scope, 'progress', movie_id, data)
