// Markup = gain / purchase; margin = gain / sale. Amounts are per unit.
export function calculatePrice(cost,field,value,quantity=1){
  const c=Number(cost),v=Number(value),q=Number(quantity);
  if(!Number.isFinite(c)||c<=0||!Number.isFinite(v)||!Number.isInteger(q)||q<0||q>1000)return null;
  const cents=x=>Math.round((x+Number.EPSILON)*100)/100;
  const sale=cents(field==='percent'?c*(1+v/100):field==='profit'?c+v:v);
  if(sale<=0||sale>100000000)return null;
  const profit=cents(sale-c);
  return {sale,profit,percent:Math.round(profit/c*10000)/100,margin:Math.round(profit/sale*10000)/100,total:cents(sale*q),profitTotal:cents(profit*q)};
}
