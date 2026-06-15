import { jsonError, runWorker } from "@/src/lib/worker";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request, { params }) {
  try {
    const { id } = await params;
    const body = await request.json();
    return Response.json(
      await runWorker([
        "split-files",
        "--id",
        id,
        "--file-ids",
        (body.fileIds || []).join(","),
        "--name",
        body.name || "",
      ])
    );
  } catch (error) {
    return jsonError(error);
  }
}
