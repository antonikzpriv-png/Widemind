import { bootstrap } from "../bootstrap.ts";
const app = await bootstrap();
console.log("Migrations applied. No opening grant or balance was created.");
app.db.close();
