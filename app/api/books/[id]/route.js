import { jsonError, runWorker } from "@/src/lib/worker";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(_request, { params }) {
  try {
    const { id } = await params;
    return Response.json(await runWorker(["get-book", "--id", id]));
  } catch (error) {
    return jsonError(error);
  }
}

export async function PATCH(request, { params }) {
  try {
    const { id } = await params;
    const body = await request.json();
    return Response.json(
      await runWorker(["update-book", "--id", id, "--payload", JSON.stringify(body)])
    );
  } catch (error) {
    return jsonError(error);
  }
}
