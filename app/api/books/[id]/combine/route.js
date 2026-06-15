import { jsonError, runWorker } from "@/src/lib/worker";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request, { params }) {
  try {
    const { id } = await params;
    const body = await request.json();
    return Response.json(
      await runWorker([
        "combine-groups",
        "--id",
        id,
        "--source-ids",
        (body.groupIds || []).join(","),
      ])
    );
  } catch (error) {
    return jsonError(error);
  }
}
