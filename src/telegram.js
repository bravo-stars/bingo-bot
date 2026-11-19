const { Bot, InlineKeyboard, Keyboard } = require("grammy");
const crypto = require("node:crypto");
const { query, withTransaction } = require("./db");

const TOKEN=process.env.BOT_TOKEN;
const BASE_URL=(process.env.RENDER_EXTERNAL_URL||"").replace(/\/$/,"");
const ADMIN_ID=String(process.env.ADMIN_ID||"7504691975");
if(!TOKEN||!BASE_URL) throw new Error("BOT_TOKEN and RENDER_EXTERNAL_URL are required");
const bot=new Bot(TOKEN);
const menu=new Keyboard().text("🎮 Play").row().text("💰 Check your balance").row().text("💳 Deposit").row().text("🏧 Withdrawal").row().text("📜 Transaction History").row().text("📖 Instruction").row().text("👤 Register").resized();
const payMenu=new Keyboard().text("📱 TELE-Birr").row().text("🏦 CBE-Birr").row().text("⬅️ Back").resized();
const cash=n=>(Number(n||0)/100).toFixed(2);
const isAdmin=ctx=>String(ctx.from?.id)===ADMIN_ID;
async function state(id){
 const r=await query("SELECT state_json FROM bot_flow_sessions WHERE user_id=$1",[String(id)]);
 try{return r.rows[0]?JSON.parse(r.rows[0].state_json):{};}catch{return {};}
}
async function await save(id,s){
 await query("INSERT INTO bot_flow_sessions(user_id,state_json,updated_at) VALUES($1,$2,$3) ON CONFLICT(user_id) DO UPDATE SET state_json=EXCLUDED.state_json,updated_at=EXCLUDED.updated_at",[String(id),JSON.stringify(s),new Date().toISOString()]);
}
async function await clear(id){await query("DELETE FROM bot_flow_sessions WHERE user_id=$1",[String(id)]);}
async function ensureUser(u){
 const name=[u.first_name,u.last_name].filter(Boolean).join(" ")||"Player";
 await query("INSERT INTO bot_users(user_id,full_name,username,registered_at) VALUES($1,$2,$3,$4) ON CONFLICT(user_id) DO UPDATE SET full_name=EXCLUDED.full_name,username=EXCLUDED.username",[String(u.id),name,u.username||null,new Date().toISOString()]);
 await query("INSERT INTO wallets(user_id,balance_cents) VALUES($1,0) ON CONFLICT(user_id) DO NOTHING",[String(u.id)]);
}
async function showBalance(ctx){
 const r=await query("SELECT balance_cents FROM wallets WHERE user_id=$1",[String(ctx.from.id)]);
 return ctx.reply("💰 Your BravoBingo balance: "+cash(r.rows[0]?.balance_cents)+" ETB");
}
async function history(ctx){
 const r=await query("SELECT kind,amount_cents,description,created_at FROM wallet_transactions WHERE user_id=$1 ORDER BY id DESC LIMIT 10",[String(ctx.from.id)]);
 return ctx.reply(r.rows.length?"📜 Recent transactions:\n"+r.rows.map(x=>String(x.created_at).slice(0,16)+" | "+x.kind+" | "+cash(x.amount_cents)+" ETB | "+x.description).join("\n"):"No transactions yet.");
}
async function depositStart(ctx){await clear(ctx.from.id);await save(ctx.from.id,{step:"deposit_method"});return ctx.reply("💳 BravoBingo Deposit\nየማስገቢያ መንገድ ይምረጡ:",{reply_markup:payMenu});}
async function withdrawStart(ctx){await clear(ctx.from.id);await save(ctx.from.id,{step:"withdraw_method"});return ctx.reply("🏧 የWithdrawal መንገድ ይምረጡ:",{reply_markup:payMenu});}
async function depositProof(ctx,s,proof,hash,fileId){
 const uid=String(ctx.from.id);
 const r=await query("INSERT INTO deposit_requests(user_id,method,sms_text,sms_hash,requested_amount_cents,created_at) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(sms_hash) DO NOTHING RETURNING id",[uid,s.method,proof,hash,s.cents,new Date().toISOString()]);
 if(!r.rows.length)return ctx.reply("ይህ SMS/Screenshot ከዚህ በፊት ተልኳል።");
 const id=r.rows[0].id;
 const caption="💳 BravoBingo Deposit Request #"+id+"\nUser ID: "+uid+"\nName: "+(ctx.from.first_name||"")+"\nUsername: @"+(ctx.from.username||"none")+"\nMethod: "+s.method+"\nRequested amount: "+cash(s.cents)+" ETB\n\nApprove: /approve_deposit "+id+" AMOUNT_ETB\nReject: /reject_deposit "+id;
 try{
  if(fileId && ctx.message?.document) await bot.api.sendDocument(ADMIN_ID,fileId,{caption:caption.slice(0,1024)});
  else if(fileId) await bot.api.sendPhoto(ADMIN_ID,fileId,{caption:caption.slice(0,1024)});
  else await bot.api.sendMessage(ADMIN_ID,caption+"\n\nPayment SMS:\n"+proof.slice(0,3000));
 }catch(e){console.error(e);return ctx.reply("ጥያቄው ተመዝግቧል፣ ግን Admin ማሳወቂያ አልተላከም።");}
 await query("DELETE FROM deposit_sessions WHERE user_id=$1",[uid]);await clear(uid);
 return ctx.reply("✅ የDeposit ጥያቄዎ #"+id+" ተልኳል። Admin ካረጋገጠ በኋላ Wallet ይጨመራል።");
}
async function approveDeposit(ctx){
 if(!isAdmin(ctx))return ctx.reply("⛔ Admin access only.");
 const p=(ctx.match||"").trim().split(/\s+/),id=Number(p[0]),amount=Number(String(p[1]||"").replace(/,/g,""));
 if(!Number.isSafeInteger(id)||id<1||!Number.isFinite(amount)||amount<10||Math.round(amount*100)!==amount*100)return ctx.reply("Usage: /approve_deposit REQUEST_ID AMOUNT_ETB (minimum 10 ETB)");
 const cents=Math.round(amount*100);
 const row=await withTransaction(async c=>{
  const q=await c.query("SELECT * FROM deposit_requests WHERE id=$1 FOR UPDATE",[id]);const d=q.rows[0];
  if(!d||d.status!=="pending")throw new Error("Request not found or already reviewed.");
  await c.query("INSERT INTO wallets(user_id,balance_cents) VALUES($1,0) ON CONFLICT(user_id) DO NOTHING",[String(d.user_id)]);
  await c.query("UPDATE wallets SET balance_cents=balance_cents+$1 WHERE user_id=$2",[cents,String(d.user_id)]);
  await c.query("UPDATE deposit_requests SET status='approved',amount_cents=$1,reviewed_at=$2 WHERE id=$3",[cents,new Date().toISOString(),id]);
  await c.query("INSERT INTO wallet_transactions(user_id,kind,amount_cents,description,created_at) VALUES($1,'DEPOSIT',$2,$3,$4)",[String(d.user_id),cents,"Deposit #"+id,new Date().toISOString()]);
  return d.user_id;
 });
 await ctx.reply("✅ Deposit #"+id+" approved: "+cash(cents)+" ETB credited.");
 try{await bot.api.sendMessage(row,"✅ Your deposit #"+id+" was approved. "+cash(cents)+" ETB added to your Wallet.");}catch{}
}
async function rejectDeposit(ctx){
 if(!isAdmin(ctx))return ctx.reply("⛔ Admin access only.");
 const id=Number((ctx.match||"").trim());if(!Number.isSafeInteger(id)||id<1)return ctx.reply("Usage: /reject_deposit REQUEST_ID");
 const r=await query("UPDATE deposit_requests SET status='rejected',reviewed_at=$1 WHERE id=$2 AND status='pending' RETURNING user_id",[new Date().toISOString(),id]);
 if(!r.rows.length)return ctx.reply("Request not found or already reviewed.");
 await ctx.reply("❌ Deposit #"+id+" rejected.");try{await bot.api.sendMessage(r.rows[0].user_id,"❌ Your deposit request #"+id+" was rejected.");}catch{}
}
async function reviewWithdrawal(ctx,paid){
 if(!isAdmin(ctx))return ctx.reply("⛔ Admin access only.");
 const id=Number((ctx.match||"").trim());if(!Number.isSafeInteger(id)||id<1)return ctx.reply("Usage: /"+(paid?"approve":"reject")+"_withdrawal REQUEST_ID");
 const row=await withTransaction(async c=>{
  const q=await c.query("SELECT * FROM withdrawal_requests WHERE id=$1 FOR UPDATE",[id]);const w=q.rows[0];
  if(!w||w.status!=="pending")throw new Error("Request not found or already reviewed.");
  await c.query("UPDATE withdrawal_requests SET status=$1,reviewed_at=$2 WHERE id=$3",[paid?"paid":"rejected",new Date().toISOString(),id]);
  if(!paid){await c.query("UPDATE wallets SET balance_cents=balance_cents+$1 WHERE user_id=$2",[w.amount_cents,String(w.user_id)]);await c.query("INSERT INTO wallet_transactions(user_id,kind,amount_cents,description,created_at) VALUES($1,'WITHDRAWAL_REFUND',$2,$3,$4)",[String(w.user_id),w.amount_cents,"Withdrawal #"+id+" refund",new Date().toISOString()]);}
  return w;
 });
 await ctx.reply(paid?"✅ Withdrawal #"+id+" marked as paid.":"❌ Withdrawal #"+id+" rejected; refunded "+cash(row.amount_cents)+" ETB.");
 try{await bot.api.sendMessage(row.user_id,paid?"✅ Withdrawal #"+id+" has been marked paid.":"❌ Withdrawal #"+id+" rejected. "+cash(row.amount_cents)+" ETB returned to your Wallet.");}catch{}
}
async function adminPanel(ctx){
 if(!isAdmin(ctx))return ctx.reply("⛔ Admin access only.");
 const kb=new InlineKeyboard().text("📊 Overview","admin:overview").text("💳 Pending deposits","admin:deposits").row().text("👥 Users","admin:users").text("📜 Transactions","admin:transactions").row().text("🎮 Game status","admin:game");
 return ctx.reply("🛠 BravoBingo ADMIN PANEL\nChoose a section:",{reply_markup:kb});
}
async function adminAction(ctx){
 if(!isAdmin(ctx))return ctx.answerCallbackQuery({text:"Admin access only.",show_alert:true});
 await ctx.answerCallbackQuery();const a=ctx.callbackQuery.data.split(":")[1];let out="";
 if(a==="menu")return ctx.editMessageText("🛠 BravoBingo ADMIN PANEL\nChoose a section:",{reply_markup:new InlineKeyboard().text("📊 Overview","admin:overview").text("💳 Pending deposits","admin:deposits").row().text("👥 Users","admin:users").text("📜 Transactions","admin:transactions").row().text("🎮 Game status","admin:game")});
 if(a==="overview"){
  const q=await Promise.all([query("SELECT COUNT(*) n FROM wallets"),query("SELECT COALESCE(SUM(balance_cents),0) n FROM wallets"),query("SELECT COUNT(*) n FROM deposit_requests WHERE status='pending'"),query("SELECT COUNT(*) n FROM round_tickets")]);
  out="📊 OVERVIEW\n👥 Wallet users: "+q[0].rows[0].n+"\n💰 Total wallet balance: "+cash(q[1].rows[0].n)+" ETB\n💳 Pending deposits: "+q[2].rows[0].n+"\n🎟 Tickets: "+q[3].rows[0].n;
 }else if(a==="deposits"){
  const q=await query("SELECT id,user_id,method,created_at FROM deposit_requests WHERE status='pending' ORDER BY id DESC LIMIT 10");
  out=q.rows.length?"💳 PENDING DEPOSITS\n"+q.rows.map(x=>"#"+x.id+" · User "+x.user_id+" · "+x.method+" · "+String(x.created_at).slice(0,16)).join("\n")+"\n\nApprove: /approve_deposit ID AMOUNT_ETB\nReject: /reject_deposit ID":"✅ No pending deposits.";
 }else if(a==="users"){
  const q=await query("SELECT user_id,balance_cents FROM wallets ORDER BY balance_cents DESC LIMIT 15");out="👥 USERS\n"+q.rows.map(x=>"User "+x.user_id+" · "+cash(x.balance_cents)+" ETB").join("\n");
 }else if(a==="transactions"){
  const q=await query("SELECT user_id,kind,amount_cents,description FROM wallet_transactions ORDER BY id DESC LIMIT 10");out="📜 LAST 10 TRANSACTIONS\n"+q.rows.map(x=>x.kind+" · User "+x.user_id+" · "+cash(x.amount_cents)+" ETB · "+x.description).join("\n");
 }else if(a==="game"){
  const q=await query("SELECT * FROM game_rounds ORDER BY id DESC LIMIT 1");if(!q.rows[0])out="No game round yet.";else{const g=q.rows[0],n=await query("SELECT COUNT(*) n FROM round_tickets WHERE round_id=$1",[g.id]);out="🎮 ROUND #"+g.id+"\nStatus: "+g.status+"\nTickets: "+n.rows[0].n+"\nNumbers called: "+JSON.parse(g.drawn_json||"[]").length+"\nPrize pool: "+cash(g.prize_cents)+" ETB";}
 }
 return ctx.editMessageText(out,{reply_markup:new InlineKeyboard().text("⬅️ Admin menu","admin:menu")});
}

bot.command("start",async ctx=>{await ensureUser(ctx.from);await clear(ctx.from.id);await ctx.reply("🎉 Welcome to BravoBingo! Choose an option:",{reply_markup:menu});});
bot.command("play",ctx=>ctx.reply("🎮 VIP ROOM — Choose up to 2 Cartelas.",{reply_markup:new InlineKeyboard().webApp("🎟 Open VIP Cartela 1–600",BASE_URL+"/")}));
bot.command("balance",showBalance);bot.command("deposit",depositStart);bot.command("withdraw",withdrawStart);bot.command("history",history);
bot.command("instructions",ctx=>ctx.reply("🎯 BravoBingo VIP Rules\n• Each Cartela costs 10 ETB.\n• Choose up to 2 Cartelas during the 30-second selection period.\n• Numbers are called every 3 seconds.\n• Row, column, diagonal, or four corners wins.\n• 80% of the ticket pool goes to winners; 20% is the house share."));
bot.command("register",async ctx=>{await ensureUser(ctx.from);await ctx.reply("✅ Your BravoBingo account is registered.");});
bot.command("approve_deposit",approveDeposit);bot.command("reject_deposit",rejectDeposit);
bot.command("approve_withdrawal",ctx=>reviewWithdrawal(ctx,true));bot.command("reject_withdrawal",ctx=>reviewWithdrawal(ctx,false));bot.command("admin",adminPanel);bot.callbackQuery(/^admin:/,adminAction);

bot.on("message:text",async ctx=>{
 const t=ctx.message.text.trim(),id=String(ctx.from.id);
 if(t.startsWith("/"))return;
 let currentState=await state(id);
 if(!currentState.step){
  const saved=await query("SELECT method,requested_amount_cents FROM deposit_sessions WHERE user_id=$1",[id]);
  if(saved.rows[0])await save(id,{step:"deposit_proof",method:saved.rows[0].method,cents:saved.rows[0].requested_amount_cents});
 }
 const s=await state(id);
 if(t==="🎮 Play")return ctx.reply("🎮 VIP ROOM — Choose up to 2 Cartelas.",{reply_markup:new InlineKeyboard().webApp("🎟 Open VIP Cartela 1–600",BASE_URL+"/")});
 if(t==="💰 Check your balance")return showBalance(ctx);
 if(t==="💳 Deposit")return depositStart(ctx);if(t==="🏧 Withdrawal")return withdrawStart(ctx);
 if(t==="📜 Transaction History")return history(ctx);
 if(t==="📖 Instruction")return ctx.reply("Each Cartela costs 10 ETB. Select up to 2 within 30 seconds. Numbers are called every 3 seconds. Row, column, diagonal or four corners wins. 80% goes to winners; 20% is house.");
 if(t==="👤 Register"){await ensureUser(ctx.from);return ctx.reply("✅ Your BravoBingo account is registered.");}
 if(t==="⬅️ Back"){await clear(id);await query("DELETE FROM deposit_sessions WHERE user_id=$1",[id]);return ctx.reply("Main menu:",{reply_markup:menu});}
 if(s.step==="deposit_method"&&["📱 TELE-Birr","🏦 CBE-Birr"].includes(t)){const m=t.includes("TELE")?"TELE-Birr":"CBE-Birr";await save(id,{step:"deposit_amount",method:m});return ctx.reply(m+"\nየሚያስገቡትን ብር መጠን ያስገቡ። (Minimum: 10 ETB)");}
 if(s.step==="deposit_amount"){
  const a=Number(t.replace(/,/g,"").replace(/\s*(ETB|birr|ብር)$/i,""));if(!Number.isFinite(a)||a<10||Math.round(a*100)!==a*100)return ctx.reply("❌ ቢያንስ 10 ETB ያስገቡ።");
  const cents=Math.round(a*100);await query("INSERT INTO deposit_sessions(user_id,method,requested_amount_cents,updated_at) VALUES($1,$2,$3,$4) ON CONFLICT(user_id) DO UPDATE SET method=EXCLUDED.method,requested_amount_cents=EXCLUDED.requested_amount_cents,updated_at=EXCLUDED.updated_at",[id,s.method,cents,new Date().toISOString()]);
  await save(id,{step:"deposit_proof",method:s.method,cents});return ctx.reply("💳 "+s.method+"\nየሚከፍሉት መጠን: "+cash(cents)+" ETB\nስልክ ቁጥር: 0945723383\nAccount Name: Samuel Adino\n\n1. ትክክለኛውን መጠን ይክፈሉ።\n2. Screenshot ወይም የደረሰዎትን SMS Copy/Paste ያድርጉ።\n3. Admin ካረጋገጠ በኋላ Wallet ይጨመራል።");
 }
 if(s.step==="deposit_proof")return depositProof(ctx,s,t,crypto.createHash("sha256").update(t).digest("hex"),null);
 if(s.step==="withdraw_method"&&["📱 TELE-Birr","🏦 CBE-Birr"].includes(t)){await save(id,{step:"withdraw_amount",method:t.includes("TELE")?"TELE-Birr":"CBE-Birr"});return ctx.reply("የሚወጣውን ብር መጠን ያስገቡ።");}
 if(s.step==="withdraw_amount"){
  const a=Number(t.replace(/,/g,"").replace(/\s*(ETB|birr|ብር)$/i,""));if(!Number.isFinite(a)||a<=0||Math.round(a*100)!==a*100)return ctx.reply("ትክክለኛ የብር መጠን ያስገቡ።");
  const cents=Math.round(a*100),w=await query("SELECT balance_cents FROM wallets WHERE user_id=$1",[id]);if(cents>Number(w.rows[0]?.balance_cents||0))return ctx.reply("Balanceዎ "+cash(w.rows[0]?.balance_cents)+" ETB ብቻ ነው።");
  await save(id,{step:"withdraw_account",method:s.method,cents});return ctx.reply("የሚቀበሉበት ቁጥር እና Account Name ይላኩ።");
 }
 if(s.step==="withdraw_account"){
  const details=t.slice(0,500),cents=s.cents;
  const rid=await withTransaction(async c=>{const u=await c.query("UPDATE wallets SET balance_cents=balance_cents-$1 WHERE user_id=$2 AND balance_cents >= $1 RETURNING balance_cents",[cents,id]);if(!u.rows.length)throw new Error("በቂ Balance የለዎትም።");const q=await c.query("INSERT INTO withdrawal_requests(user_id,method,amount_cents,account_details,created_at) VALUES($1,$2,$3,$4,$5) RETURNING id",[id,s.method,cents,details,new Date().toISOString()]);await c.query("INSERT INTO wallet_transactions(user_id,kind,amount_cents,description,created_at) VALUES($1,'WITHDRAWAL_HOLD',$2,$3,$4)",[id,-cents,"Withdrawal #"+q.rows[0].id,new Date().toISOString()]);return q.rows[0].id;});
  await clear(id);await bot.api.sendMessage(ADMIN_ID,"🏧 WITHDRAWAL #"+rid+"\nUser: "+id+"\nMethod: "+s.method+"\nAmount: "+cash(cents)+" ETB\nAccount: "+details+"\nApprove: /approve_withdrawal "+rid+"\nReject/refund: /reject_withdrawal "+rid);return ctx.reply("✅ Withdrawal #"+rid+" ተመዝግቧል። "+cash(cents)+" ETB ከWallet ተይዟል።");
 }
});
bot.on(["message:photo","message:document"],async ctx=>{
 const id=String(ctx.from.id);let s=await state(id);
 if(s.step!=="deposit_proof"){
  const saved=await query("SELECT method,requested_amount_cents FROM deposit_sessions WHERE user_id=$1",[id]);
  if(saved.rows[0]){s={step:"deposit_proof",method:saved.rows[0].method,cents:saved.rows[0].requested_amount_cents};await save(id,s);}
 }
 if(s.step!=="deposit_proof")return ctx.reply("📌 መጀመሪያ /deposit ይጫኑ።");
 const p=ctx.message.photo?.at(-1),d=ctx.message.document;if(!p&&!(d?.mime_type||"").startsWith("image/"))return;
 const f=p||d,proof="[IMAGE] file_unique_id="+f.file_unique_id+"\nCaption: "+(ctx.message.caption||"");
 return depositProof(ctx,s,proof,crypto.createHash("sha256").update(id+":"+f.file_unique_id).digest("hex"),f.file_id);
});
bot.catch(err=>console.error("Telegram bot error:",err));
module.exports={bot,ADMIN_ID};
