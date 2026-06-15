import { jsonError, runWorker } from "@/src/lib/worker";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request) {
  try {
    const { searchParams } = new URL(request.url);
    const args = [
      "list-books",
      "--status",
      searchParams.get("status") || "all",
      "--q",
      searchParams.get("q") || "",
    ];
    return Response.json(await runWorker(args));
  } catch (error) {
    return jsonError(error);
  }
}
