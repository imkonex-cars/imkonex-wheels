import {renderOrder} from '../shop.js';
const main=document.querySelector('#order-content');renderOrder(main);
window.addEventListener('hashchange',()=>renderOrder(main));
