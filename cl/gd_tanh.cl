/*
	err_y *= y * y * (-0.508262) + 1.143819
*/
__kernel //__attribute__((reqd_work_group_size(BLOCK_SIZE, BLOCK_SIZE, 1)))
void err_y_update(__global dtype *err_y, __global dtype *y)
{
 int offs = get_global_id(1) * Y + get_global_id(0);
 err_y[offs] *= y[offs] * y[offs] * (-0.508262f) + 1.143819f;
}
