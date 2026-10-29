/* Customer-specific calculator source. No network, eval or dependencies. */
(function(){'use strict';
const definition={"inputs":[{"default":8,"id":"hours","label":"Project hours","max":500,"min":0},{"default":60,"id":"rate","label":"Hourly rate","max":1000,"min":1}],"outputs":[{"id":"estimate","label":"Estimated total"}]};
function calculate(values){
 if(!values||typeof values!=='object'||Array.isArray(values)||Object.keys(values).length!==definition.inputs.length)throw new Error('Provide every required input.');
 for(const field of definition.inputs){const value=values[field.id];if(!Object.hasOwn(values,field.id)||typeof value!=='number'||!Number.isFinite(value)||value<field.min||value>field.max)throw new Error(field.label+' must be a finite number from '+field.min+' to '+field.max+'.');}
 const result={"estimate": ((values["hours"])*(values["rate"]))};
 for(const value of Object.values(result)){if(!Number.isFinite(value)||Math.abs(value)>1e15)throw new Error('Calculation is undefined or outside the supported numeric range.');}
 return result;
}
const api=Object.freeze({calculate,definition});
if(typeof module!=='undefined'&&module.exports)module.exports=api;
if(typeof globalThis!=='undefined')globalThis.WideMindCalculator=api;
if(typeof document!=='undefined'){
 const form=document.getElementById('calculator-form'),status=document.getElementById('calculator-status');
 function render(){try{const values={};for(const field of definition.inputs){const input=form.elements.namedItem(field.id);if(input.value.trim()==='')throw new Error(field.label+' is required.');values[field.id]=Number(input.value);}const valuesOut=calculate(values);for(const field of definition.outputs)document.getElementById('output-'+field.id).textContent=String(Number(valuesOut[field.id].toPrecision(12)));status.textContent='Calculated successfully.';status.dataset.state='success';}catch(error){for(const field of definition.outputs)document.getElementById('output-'+field.id).textContent='—';status.textContent=error.message;status.dataset.state='error';}}
 form.addEventListener('submit',event=>{event.preventDefault();render();});form.addEventListener('input',render);form.addEventListener('reset',()=>setTimeout(render,0));render();
}
})();
