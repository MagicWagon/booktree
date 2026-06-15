import { jsonError, runWorker } from "@/src/lib/worker";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request, { params }) {
  try {
    const { id } = await params;
    const body = await request.json().catch(() => ({}));
    return Response.json(
      await runWorker(["search", "--id", id, "--provider", body.provider || "both"])
    );
  } catch (error) {
    return jsonError(error);
  }
}
