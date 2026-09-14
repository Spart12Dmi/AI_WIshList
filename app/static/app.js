const $ = (selector) => document.querySelector(selector);
let user = null, registration = false, lists = [], currentList = null, searchController = null;
let products = new Map(), storeStates = new Map(), toastTimer;
let searchTimer, searchStarted = 0;
const categories = [
  {id:'headphones',name:'Audio & headphones',ideas:['Sony WH-1000XM5','Bose QuietComfort','JBL Tune 760NC']},
  {id:'coffee',name:'Coffee corner',ideas:['Baratza Encore','DeLonghi Dedica','Sage Bambino']},
  {id:'gaming',name:'Gaming',ideas:['Nintendo Switch OLED','Logitech G502','Sony DualSense']},
  {id:'phones',name:'Phones & tech',ideas:['Samsung Galaxy S24','Apple iPhone 16','Samsung 990 PRO 1TB']},
  {id:'sneakers',name:'Style & sneakers',ideas:['Nike Air Max 90','Adidas Samba','New Balance 574']},
  {id:'home',name:'Home & living',ideas:['Philips Hue Go','LEGO 10307','Dyson V15']}
];

function chooseCategory(category) {
  document.querySelectorAll('.category-card').forEach(node=>node.setAttribute('aria-pressed',String(node.dataset.category===category?.id)));
  $('#category-ideas').hidden=!category;
  if(!category)return;
  $('#category-title').textContent=category.name;
  $('#category-suggestions').replaceChildren(...category.ideas.map(idea=>button(idea,'',()=>{
    $('#query').value=idea;$('#query').focus();$('#search-form').scrollIntoView({block:'nearest',behavior:'smooth'});
  })));
}
for(const category of categories){
  const node=button('','category-card',()=>chooseCategory(category));node.dataset.category=category.id;node.setAttribute('aria-pressed','false');
  const image=el('img');image.src=`/static/categories/${category.id}.svg`;image.alt='';image.width=160;image.height=114;
  node.append(image,el('strong','',category.name));$('#category-grid').append(node);
}
$('#clear-category').onclick=()=>chooseCategory(null);
$('#browse-toggle').onclick=()=>{
  $('#browse-categories').hidden=!$('#browse-categories').hidden;
  $('#browse-toggle').setAttribute('aria-expanded',String(!$('#browse-categories').hidden));
};

function renderResults(){
  const selected=$('#filter-currency').value;
  const currencies=[...new Set([...products.values()].flatMap(p=>Object.keys(p.minimum_prices||{})))].sort();
  $('#filter-currency').replaceChildren(new Option('All currencies',''),...currencies.map(c=>new Option(c,c)));
  $('#filter-currency').value=currencies.includes(selected)?selected:'';
  const currency=$('#filter-currency').value;
  $('#filter-budget').disabled=!currency;
  $('#filter-note').textContent=currency?`Price filter in ${currency}. Delivery is not included.`:'Choose a currency for a price limit. Price sorting keeps different currencies separate.';
  const budget=$('#filter-budget').value===''?null:Number($('#filter-budget').value);
  const visible=[...products.values()].filter(p=>(!currency||p.minimum_prices?.[currency]!==undefined)&&(!currency||budget===null||p.minimum_prices[currency]<=budget)&&(!$('#filter-photos').checked||safeURL(p.image_url)));
  const sort=$('#result-sort').value;
  if(sort==='stores')visible.sort((a,b)=>b.offer_count-a.offer_count);
  if(sort.startsWith('price-'))visible.sort((a,b)=>{
    const ac=currency||Object.keys(a.minimum_prices||{}).sort()[0]||'',bc=currency||Object.keys(b.minimum_prices||{}).sort()[0]||'';
    return ac.localeCompare(bc)||(sort==='price-low'?1:-1)*((a.minimum_prices?.[ac]??Infinity)-(b.minimum_prices?.[bc]??Infinity));
  });
  $('#results').replaceChildren(...visible.map(p=>card(p)));
  $('#result-count').textContent=visible.length===products.size?String(products.size):`${visible.length} / ${products.size}`;
  if(!visible.length&&products.size)empty($('#results'),'No products match these filters.','Raise your budget, choose another currency, or turn off the photo filter.');
  else if(!visible.length&&searchController)showSkeletons();
}
for(const id of ['result-sort','filter-currency','filter-photos'])$(`#${id}`).addEventListener('change',renderResults);
$('#filter-budget').addEventListener('input',renderResults);
$('#filter-budget').disabled=true;
document.querySelectorAll('[name="search-mode"]').forEach(node=>node.onchange=()=>{
  $('#mode-hint').textContent=node.value==='quick'?'Up to 8 stores · fewer pages, same offer checks':'Up to 15 stores · deeper search, longer wait';
});
function showSkeletons(){
  if($('#results').children.length)return;
  for(let i=0;i<4;i++){const skeleton=el('div','skeleton');skeleton.setAttribute('aria-hidden','true');$('#results').append(skeleton);}
}
function updateProgress(){
  $('#search-elapsed').textContent=`${Math.floor((Date.now()-searchStarted)/1000)}s`;
  const done=[...storeStates.values()].filter(s=>!['queued','searching'].includes(s)).length;
  $('#store-progress').textContent=storeStates.size?`${done} / ${storeStates.size} stores checked · ${products.size} products found`:'Results appear as we find them';
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function button(text, className, callback) {
  const node = el('button', className, text); node.type = 'button';
  node.addEventListener('click', async () => { try { await callback(); } catch (error) { toast(error.message); } });
  return node;
}
function toast(message) { $('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').hidden = true, 4500); }
function price(amount, currency) { try { return new Intl.NumberFormat(undefined, {style:'currency', currency}).format(amount); } catch { return `${amount} ${currency}`; } }
function minimum(product) { const values = Object.entries(product.minimum_prices || {}); return values.length ? values.map(([c,p]) => price(p,c)).join(' / ') : product.offer_count ? 'No current available offer' : 'Price to be checked'; }
function date(seconds) { return new Date(seconds * 1000).toLocaleString(); }
function safeURL(value) { try { const u = new URL(value); return ['https:', 'http:'].includes(u.protocol) ? u.href : null; } catch { return null; } }
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers:{'Content-Type':'application/json', ...(user ? {'X-CSRF-Token':user.csrf}:{}), ...options.headers}});
  if (!response.ok) {
    let detail; try { detail = (await response.json()).detail; } catch { detail = 'Request failed'; }
    if (Array.isArray(detail)) detail = detail.map(d => d.msg).join('; ');
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}

function photo(product) {
  const wrap = el('div','image-wrap');
  const source = safeURL(product.image_url);
  if (source) {
    const image = document.createElement('img'); image.src = source; image.alt = product.title; image.loading = 'lazy'; image.referrerPolicy = 'no-referrer';
    image.addEventListener('error', () => wrap.replaceChildren(el('span','image-placeholder','✧')), {once:true});
    wrap.append(image);
  } else wrap.append(el('span','image-placeholder','✧'));
  return wrap;
}
function empty(where, title, description) { where.replaceChildren(); const box=el('div','empty'); box.append(el('span','','✧'),el('h3','',title),el('p','',description)); where.append(box); }

function card(product, item = null) {
  const node = el('article','product-card'); node.dataset.productId = product.id || '';
  node.append(photo(product));
  const copy = el('div','product-copy'), heading = el('h3');
  heading.append(button(product.title,'title-button', () => product.id ? showProduct(product.id, product.region || $('#region').value) : searchFor(product.title, item?.region)));
  copy.append(heading, el('p','price', minimum(product)), el('p','offer-count',product.offer_count ? `${product.offer_count} store offer${product.offer_count===1?'':'s'} · compare prices` : 'Saved idea · ready to discover'));
  if (item?.notes) copy.append(el('p','fine',item.notes));
  const firstOffer = product.offers?.[0];
  if (firstOffer?.mpn) copy.append(el('p','fine',`Model: ${firstOffer.mpn}`));
  if (item?.target_price) {
    const target = Number(item.target_price), observed = product.minimum_prices?.[item.target_currency];
    copy.append(el('p','fine',`Target: ${price(target,item.target_currency)}${observed && observed<=target ? ' · Within your target' : ''}`));
  }
  const actions=el('div','actions');
  actions.append(button(product.id ? 'Compare ↗' : 'Find offers ↗','quiet',()=> product.id ? showProduct(product.id,product.region || item?.region) : searchFor(product.title,item?.region)));
  if (!item) actions.append(button('+ Save','save-button',()=>saveProduct(product)));
  else actions.append(button('Remove','quiet',async()=>{await api(`/api/wishlists/${currentList}/items/${item.id}`,{method:'DELETE'}); await loadLists(currentList); toast('Removed from wishlist');}));
  if (item) actions.append(button('Edit','quiet',()=>editItem(item)));
  copy.append(actions);node.append(copy);return node;
}

function upsertProduct(product) {
  products.set(product.id,product);
  renderResults();updateProgress();
}

function openEditor(title, fields, onSave) {
  $('#edit-title').textContent=title; $('#edit-fields').replaceChildren(); $('#edit-error').textContent='';
  for(const field of fields){
    const label=el('label','',field.label); let input;
    if(field.options){input=el('select');for(const option of field.options){const node=el('option','',option.label);node.value=option.value;input.append(node);}}
    else input=el(field.multiline?'textarea':'input');
    input.name=field.name; input.value=field.value ?? field.options?.[0]?.value ?? ''; input.required=Boolean(field.required);
    if(field.type) input.type=field.type; if(field.max) input.maxLength=field.max;
    if(field.type==='number'){input.min='0.01';input.step='0.01';}
    label.append(input);$('#edit-fields').append(label);
  }
  $('#edit-form').onsubmit=async(event)=>{event.preventDefault();const submit=$('#edit-form .primary');submit.disabled=true;
    try{await onSave(Object.fromEntries(new FormData(event.target)));$('#edit-dialog').close();}catch(error){$('#edit-error').textContent=error.message;}finally{submit.disabled=false;}};
  $('#edit-dialog').showModal();
}
$('#close-edit').onclick=()=>$('#edit-dialog').close();

async function saveProduct(product) {
  lists = await api('/api/wishlists');
  if(!lists.length){const list=await api('/api/wishlists',{method:'POST',body:JSON.stringify({name:'My wishlist'})});lists=[list];}
  openEditor('Keep this one.',[
    {name:'list',label:'Wishlist',options:lists.map(l=>({value:l.id,label:l.name}))},
    {name:'notes',label:'A note for later',max:2000},
    {name:'target_price',label:'Target price (optional)',type:'number'},
    {name:'target_currency',label:'Target currency',value:Object.keys(product.minimum_prices||{})[0]||'CZK',options:['CZK','EUR','USD','GBP','PLN'].map(c=>({value:c,label:c}))}
  ],async(data)=>{
    await api(`/api/wishlists/${data.list}/items`,{method:'POST',body:JSON.stringify({product_id:product.id,notes:data.notes,region:product.region||$('#region').value,target_price:data.target_price||null,target_currency:data.target_price?data.target_currency:null})});
    toast('Added to your wishlist');
  });
}

async function showProduct(id, region='global') {
  const product = await api(`/api/products/${id}?region=${encodeURIComponent(region || 'global')}`);
  const root=$('#product-detail');root.replaceChildren();
  const top=el('div','detail-top'), intro=el('div');
  intro.append(el('p','eyebrow','THE DETAILS'),el('h2','',product.title),el('p','price',minimum(product)),el('p','fine','Lowest recently observed price per currency. Delivery is not included.'));
  const actions=el('div','actions');actions.append(button('+ Save to wishlist','save-button',()=>saveProduct(product)),button('Refresh offers ↗','quiet',()=>{$('#product-dialog').close();return searchFor(product.title,region);}));intro.append(actions);
  top.append(photo(product),intro);root.append(top,el('h3','',`${product.offer_count} store offers`));
  for(const offer of product.offers){
    const row=el('div','offer-row'),left=el('div'),right=el('div');
    left.append(el('strong','',offer.shop),el('p','',`${offer.availability||'Stock not provided'} · ${offer.stale?'Older observation · ':''}Checked ${date(offer.checked_at)}`));
    const variant = [offer.mpn && `Model: ${offer.mpn}`, offer.color, offer.size && `Size: ${offer.size}`].filter(Boolean).join(' · ');
    if (variant) left.append(el('p','fine',variant));
    right.append(el('strong','',price(offer.price,offer.currency)));
    const url=safeURL(offer.url);if(url){const link=el('a','quiet','Visit shop ↗');link.href=url;link.target='_blank';link.rel='noopener noreferrer';right.append(link);}
    const source=safeURL(offer.source_url);if(source&&source!==url){const link=el('a','fine','Price source');link.href=source;link.target='_blank';link.rel='noopener noreferrer';left.append(link);}
    row.append(left,right);root.append(row);
  }
  root.append(el('p','fine','Offers are grouped by a product identifier or an exact normalized title. Compare model, size and colour before buying.'));
  if(!$('#product-dialog').open) $('#product-dialog').showModal();
}
$('#close-product').onclick=()=>$('#product-dialog').close();

async function loadLists(preferred) {
  lists=await api('/api/wishlists'); currentList=Number(preferred)||currentList||lists[0]?.id;
  if(!lists.some(l=>l.id===currentList))currentList=lists[0]?.id;
  $('#list-tabs').replaceChildren();
  for(const list of lists) $('#list-tabs').append(button(`${list.name} (${list.item_count})`,list.id===currentList?'selected':'',()=>loadLists(list.id)));
  $('#list-actions').replaceChildren();$('#wishlist-items').replaceChildren();
  if(!currentList){empty($('#wishlist-items'),'A fresh start.','Create a wishlist to collect products and ideas.');return;}
  const list=await api(`/api/wishlists/${currentList}`);
  $('#list-actions').append(el('h2','',list.name),button('+ Add an idea','quiet',()=>manualItem()),button('Rename','quiet',()=>openEditor('Rename wishlist',[{name:'name',label:'Name',value:list.name,required:true,max:80}],async(data)=>{await api(`/api/wishlists/${list.id}`,{method:'PATCH',body:JSON.stringify(data)});await loadLists(list.id);})),button('Delete list','quiet',async()=>{
    if(!confirm(`Delete “${list.name}” and its saved items?`))return;
    await api(`/api/wishlists/${list.id}`,{method:'DELETE'});await loadLists();
  }));
  if(!list.items.length)empty($('#wishlist-items'),'Your collection starts here.','Save a discovery or add an idea to find later.');
  for(const item of list.items)$('#wishlist-items').append(card(item.product||{title:item.title},item));
}
function manualItem(){openEditor('An idea for later.',[{name:'title',label:'What would you like?',required:true,max:200},{name:'notes',label:'Notes',multiline:true,max:2000}],async(data)=>{await api(`/api/wishlists/${currentList}/items`,{method:'POST',body:JSON.stringify({...data,region:$('#region').value})});await loadLists(currentList);});}
function editItem(item){openEditor('Make a note.',[
  {name:'notes',label:'Notes',multiline:true,max:2000,value:item.notes},
  {name:'target_price',label:'Target price (optional)',type:'number',value:item.target_price},
  {name:'target_currency',label:'Target currency',value:item.target_currency||'CZK',options:['CZK','EUR','USD','GBP','PLN'].map(c=>({value:c,label:c}))}
],async(data)=>{await api(`/api/wishlists/${currentList}/items/${item.id}`,{method:'PATCH',body:JSON.stringify({notes:data.notes,target_price:data.target_price||null,target_currency:data.target_price?data.target_currency:null})});await loadLists(currentList);});}
$('#create-list').onclick=()=>openEditor('A new collection.',[{name:'name',label:'Wishlist name',required:true,max:80}],async(data)=>{const list=await api('/api/wishlists',{method:'POST',body:JSON.stringify(data)});await loadLists(list.id);});

async function showTab(tab){
  if(!user)return;
  for(const name of ['discover','wishlists','history'])$(`#${name}-view`).hidden=name!==tab;
  document.querySelectorAll('[data-tab]').forEach(n=>n.classList.toggle('active',n.dataset.tab===tab));
  if(tab==='wishlists')await loadLists();
  if(tab==='history'){
    const history=await api('/api/history');$('#history-list').replaceChildren();
    if(!history.length)empty($('#history-list'),'No searches yet.','Your search history will appear here.');
    for(const run of history){const row=el('div','history-row'),body=el('div');body.append(el('strong','',run.query),el('p','',`${run.region} · ${date(run.started_at)} · ${run.status} · ${run.result_count} products`));row.append(body,button('Search again ↗','quiet',()=>searchFor(run.query,run.region)));$('#history-list').append(row);}
  }
}
document.querySelectorAll('[data-tab]').forEach(n=>n.onclick=()=>showTab(n.dataset.tab).catch(e=>toast(e.message)));

function drawStores(){ $('#stores-list').replaceChildren(); for(const [domain,state] of storeStates)$('#stores-list').append(el('span','',`${domain} · ${state}`)); }
function warning(message){if([...$('#warnings').children].some(n=>n.textContent===message))return;$('#warnings').append(el('p','',message));}
function streamEvent(name,payload){
  if(name==='product_removed'){
    products.delete(payload.product_id);
    renderResults();
  }
  if(name==='status'){$('#status').textContent=payload.message;$('#progress-label').textContent=products.size?'Comparing offers':'Finding your products';}
  if(name==='stores'){storeStates=new Map(payload.stores.map(s=>[s,'queued']));drawStores();}
  if(name==='store'){storeStates.set(payload.domain,payload.status);drawStores();updateProgress();}
  if(name==='warning')warning(payload.message);
  if(name==='retrieval'){
    $('#sources').replaceChildren(el('p','fine',`${payload.sources.length} historical sources retrieved for query context.`));
    for(const source of payload.sources){const url=safeURL(source.url);if(!url)continue;const link=el('a','fine',new URL(url).hostname+' · '+date(source.checked_at));link.href=url;link.target='_blank';link.rel='noopener noreferrer';const p=el('p');p.append(link);$('#sources').append(p);}
  }
  if(name==='product'){upsertProduct(payload.product);$('#status').textContent=`${products.size} products found. Checking the remaining stores…`;}
  if(name==='complete'){
    products=new Map(payload.products.map(product=>[product.id,product]));renderResults();
    for(const note of payload.warnings||[])warning(note);
    $('#pipeline').replaceChildren(...(payload.pipeline||[]).map(s=>el('span','',s)));
    $('#status').textContent=`Search complete · ${products.size} products found for “${payload.query}”.`;
    if(!products.size)empty($('#results'),'No matching offers this time.','Try a model name or save your idea and come back later.');
  }
  if(name==='error')throw new Error(payload.message);
}

async function consume(response){
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',complete=false;
  try{while(true){const{value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});
    buffer=buffer.replace(/\r\n/g,'\n');const blocks=buffer.split('\n\n');buffer=blocks.pop();
    for(const block of blocks){const lines=block.split('\n'),event=lines.find(l=>l.startsWith('event:'))?.slice(6).trim();const data=lines.filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trim()).join('\n');
      if(event&&data){streamEvent(event,JSON.parse(data));if(event==='complete')complete=true;}}
    if(done)break;
  }if(!complete)throw new Error('Connection ended before search completion. Received products remain available.');}
  finally{reader.releaseLock();}
}
async function searchFor(query, selectedRegion){await showTab('discover');$('#query').value=query;if(selectedRegion)$('#region').value=selectedRegion;$('#search-form').requestSubmit();}
$('#search-form').onsubmit=async(event)=>{
  event.preventDefault();if(searchController)return;
  const query=$('#query').value.trim();if(query.length<2)return;
  products=new Map();storeStates=new Map();$('#results').replaceChildren();$('#warnings').replaceChildren();$('#pipeline').replaceChildren();$('#sources').replaceChildren();$('#stores-list').replaceChildren();
  $('#search-details').hidden=false;$('#status').textContent='Starting search…';$('#result-count').textContent='Searching';
  $('#search-button').disabled=true;$('#cancel-search').hidden=false;
  searchController=new AbortController();
  $('#discover-view .hero').hidden=true;$('#browse-categories').hidden=true;$('#browse-toggle').hidden=false;$('#browse-toggle').setAttribute('aria-expanded','false');
  searchStarted=Date.now();$('#search-progress').hidden=false;$('#results').setAttribute('aria-busy','true');showSkeletons();updateProgress();searchTimer=setInterval(updateProgress,1000);
  document.querySelectorAll('.category-card, #category-suggestions button, [name="search-mode"], #region').forEach(node=>node.disabled=true);
  try{
    const response=await fetch('/api/search/stream',{method:'POST',signal:searchController.signal,headers:{'Content-Type':'application/json','X-CSRF-Token':user.csrf},body:JSON.stringify({query,region:$('#region').value,max_results:20,search_mode:document.querySelector('[name="search-mode"]:checked').value})});
    if(!response.ok){const body=await response.json();throw new Error(typeof body.detail==='string'?body.detail:'Cannot start search');}
    await consume(response);
  }catch(error){$('#status').textContent=error.name==='AbortError'?'Search stopped. Products already found are kept.':error.message;}
  finally{searchController=null;clearInterval(searchTimer);$('#search-progress').hidden=true;$('#results').setAttribute('aria-busy','false');document.querySelectorAll('#results .skeleton').forEach(node=>node.remove());if(!products.size&&!$('#results .empty'))empty($('#results'),'Nothing to show yet.','Try a specific model or choose More stores to search further.');$('#search-button').disabled=false;$('#cancel-search').hidden=true;document.querySelectorAll('.category-card, #category-suggestions button, [name="search-mode"], #region').forEach(node=>node.disabled=false);}
};
$('#cancel-search').onclick=()=>searchController?.abort();
$('#region').onchange=()=>{try{localStorage.setItem('wishwise-region',$('#region').value);}catch{}};

async function signedIn(){
  $('#auth-view').hidden=true;$('#workspace').hidden=false;$('#nav').hidden=false;$('#account').hidden=false;$('#username').textContent=user.name;
  await showTab('discover');
}
$('#auth-toggle').onclick=()=>{registration=!registration;$('#name-field').hidden=!registration;$('#auth-name').required=registration;$('#auth-title').textContent=registration?'Make it yours.':'Welcome back.';$('#auth-intro').textContent=registration?'Create your personal collection.':'Sign in to pick up where you left off.';$('#auth-submit').textContent=registration?'Create account':'Sign in';$('#auth-toggle').textContent=registration?'Already have an account? Sign in':'New here? Create an account';$('#auth-password').autocomplete=registration?'new-password':'current-password';$('#auth-error').textContent='';};
$('#auth-form').onsubmit=async(event)=>{event.preventDefault();$('#auth-submit').disabled=true;$('#auth-error').textContent='';try{user=await api('/api/auth/'+(registration?'register':'login'),{method:'POST',body:JSON.stringify({email:$('#auth-email').value,password:$('#auth-password').value,...(registration?{name:$('#auth-name').value}:{})})});$('#auth-password').value='';await signedIn();}catch(error){$('#auth-error').textContent=error.message;}finally{$('#auth-submit').disabled=false;}};
$('#logout').onclick=async()=>{try{searchController?.abort();await api('/api/auth/logout',{method:'POST'});user=null;$('#workspace').hidden=true;$('#auth-view').hidden=false;$('#nav').hidden=true;$('#account').hidden=true;products.clear();$('#query').value='';$('#filter-budget').value='';$('#filter-photos').checked=false;$('#result-count').textContent='0';$('#status').textContent='Pick a category or search for a product.';$('#search-details').hidden=true;$('#browse-categories').hidden=false;$('#discover-view .hero').hidden=false;$('#browse-toggle').hidden=true;chooseCategory(null);clearTimeout(toastTimer);$('#toast').hidden=true;renderResults();empty($('#results'),'Something good is out there.','Choose a category or enter a product to compare offers.');}catch(error){toast(error.message);}};

async function boot(){
  const regions=await api('/api/regions');for(const r of regions){const option=el('option','',r.label);option.value=r.key;$('#region').append(option);}
  let saved;try{saved=localStorage.getItem('wishwise-region');}catch{}$('#region').value=regions.some(r=>r.key===saved)?saved:'czechia';
  try{user=await api('/api/auth/me');await signedIn();}catch{}
  try{const health=await api('/api/health');$('#model-status').textContent=health.ollama==='ready'?'Local AI ready':'Basic search · model offline';}catch{$('#model-status').textContent='Server unavailable';}
}
boot().catch(error=>{$('#auth-error').textContent=error.message;});
