export default function Loading() {
  return (
    <div
      className="min-h-screen flex items-center justify-center"
      style={{ background: "#f0f5fb" }}
      aria-busy="true"
      aria-label="読み込み中"
    >
      <div className="flex flex-col items-center gap-3">
        <div className="w-8 h-8 border-4 border-green-700 border-t-transparent rounded-full animate-spin motion-reduce:animate-none" />
        <p className="text-sm text-gray-500">読み込み中...</p>
      </div>
    </div>
  );
}
