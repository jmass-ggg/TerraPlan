export function useRouter() {
  return { push: () => undefined };
}

export function useParams<T>() {
  return {} as T;
}
