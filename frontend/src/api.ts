export type Row = Record<string, any>;

export async function request(path: string, data?: unknown, method?: string) {
  const response = await fetch(path, {
    method: method || (data ? "POST" : "GET"),
    headers:
      data instanceof FormData ? {} : { "Content-Type": "application/json" },
    body: data
      ? data instanceof FormData
        ? data
        : JSON.stringify(data)
      : undefined,
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "Sunucuya ulaşılamadı" }));
    throw new Error(
      typeof error.detail === "string"
        ? error.detail
        : "Form bilgileri geçersiz. Zorunlu alanları, tarihleri ve sayısal sınırları kontrol edin.",
    );
  }
  return response.status === 204 ? null : response.json();
}
