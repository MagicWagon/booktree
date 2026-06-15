import { jsonError, runWorker } from "@/src/lib/worker";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request, { params }) {
  try {
    const { id } = await params;
    const body = await request.json();
    return Response.json(
      await runWorker([
        "move-files",
        "--id",
        id,
        "--target-id",
        String(body.targetId),
        "--file-ids",
        (body.fileIds || []).join(","),
      ])
    );
  } catch (error) {
    return jsonError(error);
  }
}
