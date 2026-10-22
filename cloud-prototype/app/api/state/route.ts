import {state} from '../../../lib/hunter';
export async function GET(){try{return Response.json(await state(),{headers:{'Cache-Control':'no-store'}})}catch{return Response.json({error:'Database is not ready'}, {status:503})}}
